use crate::dialect::{self, ShaderDialect};
use crate::shader;
use crate::texture::{ChannelTexture, ProceduralKind};

#[repr(C)]
#[derive(Copy, Clone, bytemuck::Pod, bytemuck::Zeroable)]
struct GlobalsUniform {
    resolution: [f32; 4],
    mouse: [f32; 4],
    time: f32,
    time_delta: f32,
    frame: i32,
    _pad0: f32,
    date: [f32; 4],
    sample_rate: f32,
    _pad1: f32,
    _pad2: f32,
    _pad3: f32,
    channel_resolution: [[f32; 4]; 4],
    /// Backs GLSL's `float iChannelTime[4]`. std140 gives every element of
    /// a scalar array its own 16-byte slot (same rule that already applies
    /// to `channel_resolution`'s vec4s, just less visible for a lone
    /// float) — each entry here only ever uses its first `f32`, the other
    /// three exist purely to match that stride so this struct's raw bytes
    /// line up with what the shader's uniform block declares.
    channel_time: [[f32; 4]; 4],
}

/// Shadertoy has no real audio input in this engine; 44100 Hz matches the
/// value Shadertoy itself falls back to when no audio channel is bound.
const DEFAULT_SAMPLE_RATE: f32 = 44100.0;

/// Final Image pass only: this is what gets read back to bytes and hits the
/// viewport widget, so it stays 8-bit — no reader benefits from more here.
const OUTPUT_FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Rgba8Unorm;

/// Buffer A-D ping-pong targets. 16-bit float (not Shadertoy's optional
/// 32-bit) is the pragmatic default: it's filterable on every wgpu backend
/// without requesting `FLOAT32_FILTERABLE`, which isn't guaranteed to be
/// available on all adapters, and it already gives feedback/trail effects
/// (accumulation, HDR glow, physics-in-a-texture) far more headroom than
/// 8-bit before banding/clamping shows up. Sampling code (`resolve_view`,
/// the shared filtering `sampler`) is unaffected: format only changes how
/// each texel's bits are stored, not the binding layout.
const BUFFER_FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Rgba16Float;
/// Bytes per texel for `BUFFER_FORMAT`, used to size the zero-fill written
/// at buffer creation (4 channels × 2 bytes/half-float).
const BUFFER_BYTES_PER_PIXEL: u32 = 8;

/// Shadertoy-style multi-pass indices: Buffer A-D feed into each other and
/// into the final Image pass, which is the only one actually shown.
pub const PASS_BUFFER_A: usize = 0;
pub const PASS_BUFFER_B: usize = 1;
pub const PASS_BUFFER_C: usize = 2;
pub const PASS_BUFFER_D: usize = 3;
pub const PASS_IMAGE: usize = 4;
pub const NUM_BUFFERS: usize = 4;
pub const NUM_PASSES: usize = 5;

/// What a single iChannel slot of a single pass currently samples from.
enum ChannelInput {
    Empty,
    Image(ChannelTexture),
    Procedural(ChannelTexture),
    Buffer(usize),
    Cubemap(ChannelTexture),
    /// Shadertoy's `iKeyboard`. Unlike `Image`/`Procedural`/`Cubemap`,
    /// this doesn't own its texture — there's only one keyboard, so every
    /// slot assigned to it (any pass, any of the 4 indices) samples the
    /// same shared `Engine::keyboard_texture`, exactly like `Buffer(usize)`
    /// shares one of the 4 ping-pong targets.
    Keyboard,
    /// A video file or webcam, decoded frame-by-frame on the Python side
    /// (Qt has its own decoders for both — see `video_source.py`) and
    /// pushed in here via `update_ichannel_video_frame`. The engine itself
    /// never opens a file or a camera device; this is just a plain
    /// `Rgba8Unorm` texture that gets re-uploaded instead of uploaded once,
    /// plus that source's own playback position in seconds (Shadertoy's
    /// `iChannelTime`, meaningless — and left at 0 — for every other
    /// channel kind).
    Video(ChannelTexture, f32),
    /// An audio file, decoded/FFT'd on the Python side (Qt has no FFT of
    /// its own — see `audio_source.py`) and pushed in here via
    /// `update_ichannel_audio_frame`: a fixed 512x2 texture (row 0 =
    /// spectrum, row 1 = waveform, see `texture::ChannelTexture::audio`)
    /// re-uploaded every tick, plus that source's own playback position in
    /// seconds (`iChannelTime`, same convention as `Video`).
    Audio(ChannelTexture, f32),
}

/// A ping-pong render target backing one of the Buffer A-D passes: buffers
/// can read their own previous frame (self-feedback / trails), so each one
/// needs two physical textures. `latest` tracks which of the two holds the
/// most recently completed frame; the other is written into next.
struct PingPongTarget {
    // Never read directly (only `views` is), but must be kept alive here:
    // dropping a wgpu::Texture invalidates any TextureView still pointing
    // at it.
    #[allow(dead_code)]
    textures: [wgpu::Texture; 2],
    views: [wgpu::TextureView; 2],
    latest: usize,
}

impl PingPongTarget {
    fn new(device: &wgpu::Device, queue: &wgpu::Queue, width: u32, height: u32) -> Self {
        let make = |label: &str| {
            let texture = device.create_texture(&wgpu::TextureDescriptor {
                label: Some(label),
                size: wgpu::Extent3d {
                    width,
                    height,
                    depth_or_array_layers: 1,
                },
                mip_level_count: 1,
                sample_count: 1,
                dimension: wgpu::TextureDimension::D2,
                format: BUFFER_FORMAT,
                usage: wgpu::TextureUsages::RENDER_ATTACHMENT
                    | wgpu::TextureUsages::TEXTURE_BINDING
                    | wgpu::TextureUsages::COPY_DST,
                view_formats: &[],
            });
            // Clear to transparent black up front so a buffer that reads
            // itself before ever being rendered (frame 0 self-feedback)
            // samples defined data instead of driver-dependent garbage.
            // All-zero bytes still mean 0.0 under IEEE-754 half-float, same
            // as they did for the previous 8-bit unorm format, so this
            // zero-fill needs no numeric change — only the row size does.
            let zeros = vec![0u8; (width * height * BUFFER_BYTES_PER_PIXEL) as usize];
            queue.write_texture(
                wgpu::ImageCopyTexture {
                    texture: &texture,
                    mip_level: 0,
                    origin: wgpu::Origin3d::ZERO,
                    aspect: wgpu::TextureAspect::All,
                },
                &zeros,
                wgpu::ImageDataLayout {
                    offset: 0,
                    bytes_per_row: Some(BUFFER_BYTES_PER_PIXEL * width),
                    rows_per_image: Some(height),
                },
                wgpu::Extent3d {
                    width,
                    height,
                    depth_or_array_layers: 1,
                },
            );
            let view = texture.create_view(&wgpu::TextureViewDescriptor::default());
            (texture, view)
        };
        let (tex_a, view_a) = make("buffer-pingpong-a");
        let (tex_b, view_b) = make("buffer-pingpong-b");
        Self {
            textures: [tex_a, tex_b],
            views: [view_a, view_b],
            latest: 0,
        }
    }

    fn latest_view(&self) -> &wgpu::TextureView {
        &self.views[self.latest]
    }

    fn write_view(&self) -> &wgpu::TextureView {
        &self.views[1 - self.latest]
    }

    fn flip(&mut self) {
        self.latest = 1 - self.latest;
    }
}

fn create_output_texture(device: &wgpu::Device, width: u32, height: u32) -> (wgpu::Texture, wgpu::TextureView) {
    let texture = device.create_texture(&wgpu::TextureDescriptor {
        label: Some("viewport-output"),
        size: wgpu::Extent3d {
            width,
            height,
            depth_or_array_layers: 1,
        },
        mip_level_count: 1,
        sample_count: 1,
        dimension: wgpu::TextureDimension::D2,
        format: OUTPUT_FORMAT,
        usage: wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::COPY_SRC,
        view_formats: &[],
    });
    let view = texture.create_view(&wgpu::TextureViewDescriptor::default());
    (texture, view)
}

/// Builds the 2 bind-group-layout entries (texture + sampler) for one
/// iChannel slot. `kind` picks the `view_dimension` wgpu will validate the
/// actually-bound texture (and the shader's `texture2D`/`textureCube`
/// declaration, see `shader::build_fragment_source`) against — all three
/// must agree, which is exactly why this layout is now rebuilt per pass,
/// per compile, from that pass's current channel kinds, rather than once
/// and shared globally as before cubemaps existed.
fn channel_binding_entry(
    binding_tex: u32,
    binding_sampler: u32,
    kind: shader::ChannelKind,
) -> [wgpu::BindGroupLayoutEntry; 2] {
    let view_dimension = match kind {
        shader::ChannelKind::D2 => wgpu::TextureViewDimension::D2,
        shader::ChannelKind::Cube => wgpu::TextureViewDimension::Cube,
    };
    [
        wgpu::BindGroupLayoutEntry {
            binding: binding_tex,
            visibility: wgpu::ShaderStages::FRAGMENT,
            ty: wgpu::BindingType::Texture {
                sample_type: wgpu::TextureSampleType::Float { filterable: true },
                view_dimension,
                multisampled: false,
            },
            count: None,
        },
        wgpu::BindGroupLayoutEntry {
            binding: binding_sampler,
            visibility: wgpu::ShaderStages::FRAGMENT,
            ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Filtering),
            count: None,
        },
    ]
}

pub struct Engine {
    device: wgpu::Device,
    queue: wgpu::Queue,
    width: u32,
    height: u32,
    /// `adapter.limits().max_texture_dimension_2d`, i.e. the *actual*
    /// per-axis texture size this specific machine's GPU/driver supports —
    /// see `Engine::new`'s comment on why `required_limits` is requested
    /// from the adapter rather than `wgpu::Limits::downlevel_defaults()`.
    /// Exposed to Python (`lib.rs::Engine::max_texture_dimension`) so a
    /// resolution the UI is about to ask for (video export, RM10.md
    /// section 1 item 8) can be checked *before* it reaches `wgpu` and
    /// panics on a validation error instead of returning a `Result`.
    max_texture_dimension: u32,

    output_texture: wgpu::Texture,
    output_view: wgpu::TextureView,
    buffers: [PingPongTarget; NUM_BUFFERS],

    // One bind group layout / pipeline layout *per pass* (not shared
    // globally): each pass's 4 iChannel slots can independently be plain
    // 2D or cubemap, and wgpu requires the pipeline's layout to exactly
    // match what the compiled shader declares for each binding. Populated
    // together with `pipelines[pass]` inside `compile_pass`; `None` until
    // that pass has compiled at least once.
    bind_group_layouts: [Option<wgpu::BindGroupLayout>; NUM_PASSES],
    // Not read back after pipeline creation (wgpu bakes the layout into
    // the pipeline itself) — kept alive here anyway defensively, same
    // reasoning as `PingPongTarget::textures` above.
    #[allow(dead_code)]
    pipeline_layouts: [Option<wgpu::PipelineLayout>; NUM_PASSES],
    sampler: wgpu::Sampler,
    placeholder: ChannelTexture,
    /// Shared `iKeyboard` state texture (256x3, see `texture::ChannelTexture::keyboard`),
    /// re-uploaded on demand by `update_keyboard` whenever the UI reports a
    /// key event — not per-pass, not per-slot, there's only one of these.
    keyboard_texture: ChannelTexture,

    globals_buffer: wgpu::Buffer,

    common_src: String,
    channels: [[ChannelInput; 4]; NUM_PASSES],
    /// Last successfully compiled `mainImage` source per pass (pre-Common,
    /// pre-harness), kept so a channel-kind change (see
    /// `set_channel_input`) can silently recompile that pass without the
    /// Python side having to call `compile_pass` again itself.
    pass_sources: [Option<String>; NUM_PASSES],
    pipelines: [Option<wgpu::RenderPipeline>; NUM_PASSES],

    /// Dernier dialecte détecté par pass (voir `dialect.rs`), utilisé à la
    /// fois pour choisir quel chemin de compilation appliquer
    /// (`shader::build_fragment_source` vs `build_fragment_source_standalone`)
    /// et comme "previous" lors du prochain `compile_pass`, exactement
    /// comme `main_window.py::_pass_dialects` côté footer — les deux sont
    /// calculés indépendamment par la même fonction pure sur le même
    /// texte, donc toujours d'accord, sans qu'aucun des deux ait besoin de
    /// connaître l'existence de l'autre.
    pass_dialects: [Option<ShaderDialect>; NUM_PASSES],
    /// Buffers UBO zero-fill (16 octets chacun, voir
    /// `shader::CustomUniformDecl`) backant les `uniform` personnalisés
    /// détectés en mode GLSL standalone pour le dernier compile réussi de
    /// chaque pass, avec leur binding assigné — réutilisés par
    /// `build_bind_group` à chaque frame (pas de GPU work par frame, la
    /// même paire `(binding, Buffer)` est simplement re-référencée).
    custom_uniform_buffers: [Vec<(u32, wgpu::Buffer)>; NUM_PASSES],

    /// The previous call's readback, still in flight (submitted to the GPU
    /// but not necessarily mapped yet). `render()` resolves this *before*
    /// blocking on the frame it just submitted, so the CPU only stalls on
    /// `device.poll(Wait)` when the GPU is genuinely slower than the
    /// caller's frame cadence — normally the map callback has long since
    /// fired by the time the next `render()` call comes in a frame later,
    /// so the "wait" degrades to an instant no-op. This trades one frame
    /// of latency (imperceptible for a live preview) for decoupling GPU
    /// submission from CPU readback, avoiding the UI-thread micro-freeze a
    /// single synchronous map-and-wait causes every frame at high
    /// resolution.
    pending_readback: Option<PendingReadback>,
}

struct PendingReadback {
    buffer: wgpu::Buffer,
    receiver: std::sync::mpsc::Receiver<Result<(), wgpu::BufferAsyncError>>,
    width: u32,
    height: u32,
    padded_bytes_per_row: u32,
}

impl Engine {
    pub fn new(width: u32, height: u32) -> Result<Self, String> {
        let instance = wgpu::Instance::new(wgpu::InstanceDescriptor {
            backends: wgpu::Backends::PRIMARY,
            ..Default::default()
        });
        let adapter = pollster::block_on(instance.request_adapter(&wgpu::RequestAdapterOptions {
            power_preference: wgpu::PowerPreference::HighPerformance,
            compatible_surface: None,
            force_fallback_adapter: false,
        }))
        .ok_or("aucun adaptateur graphique wgpu disponible")?;

        // `wgpu::Limits::downlevel_defaults()` was the previous value here —
        // a WebGL2-compatible profile that caps `max_texture_dimension_2d`
        // at 2048 regardless of what the real adapter actually supports.
        // That silently broke every export/texture above 2048px on *any*
        // machine (verified: `Engine::new(20000, 20000)` panics inside
        // `wgpu`'s validation with "Dimension X value 20000 exceeds the
        // limit of 2048" even on a modern desktop GPU) despite
        // `export_video_dialog.py` already advertising resolutions up to
        // 7680 (8K). Requesting the adapter's own reported limits instead
        // is always satisfiable (that's what "the adapter reports" means)
        // and gives this native desktop app the GPU's actual capability
        // rather than an artificially conservative one.
        let adapter_limits = adapter.limits();
        let (device, queue) = pollster::block_on(adapter.request_device(
            &wgpu::DeviceDescriptor {
                label: Some("shadertoy-device"),
                required_features: wgpu::Features::empty(),
                required_limits: adapter_limits.clone(),
            },
            None,
        ))
        .map_err(|e| format!("impossible de créer le device wgpu: {e}"))?;
        let max_texture_dimension = adapter_limits.max_texture_dimension_2d;

        // Same reasoning/mechanism as `Engine::resize`'s doc comment: the
        // *initial* resolution (Python's `Engine(width, height)`, e.g. the
        // headless `--export-mp4` CLI's `--width`/`--height`) is just as
        // capable of exceeding available VRAM as a later `resize()` call,
        // and needs the same error-scope capture instead of letting a
        // too-large allocation panic during construction.
        device.push_error_scope(wgpu::ErrorFilter::OutOfMemory);
        device.push_error_scope(wgpu::ErrorFilter::Validation);

        let (output_texture, output_view) = create_output_texture(&device, width, height);

        let buffers = [
            PingPongTarget::new(&device, &queue, width, height),
            PingPongTarget::new(&device, &queue, width, height),
            PingPongTarget::new(&device, &queue, width, height),
            PingPongTarget::new(&device, &queue, width, height),
        ];

        let validation_err = pollster::block_on(device.pop_error_scope());
        let oom_err = pollster::block_on(device.pop_error_scope());
        if let Some(e) = validation_err.or(oom_err) {
            return Err(format!(
                "impossible d'initialiser le moteur à {width}x{height} : mémoire graphique insuffisante ({e})"
            ));
        }

        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            label: Some("default-sampler"),
            address_mode_u: wgpu::AddressMode::Repeat,
            address_mode_v: wgpu::AddressMode::Repeat,
            address_mode_w: wgpu::AddressMode::Repeat,
            mag_filter: wgpu::FilterMode::Linear,
            min_filter: wgpu::FilterMode::Linear,
            mipmap_filter: wgpu::FilterMode::Linear,
            ..Default::default()
        });
        let placeholder = ChannelTexture::placeholder(&device, &queue);
        let keyboard_texture = ChannelTexture::keyboard(&device, &queue);

        let globals_buffer = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("globals-ubo"),
            size: std::mem::size_of::<GlobalsUniform>() as u64,
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });

        Ok(Self {
            device,
            queue,
            width,
            height,
            max_texture_dimension,
            output_texture,
            output_view,
            buffers,
            bind_group_layouts: std::array::from_fn(|_| None),
            pipeline_layouts: std::array::from_fn(|_| None),
            sampler,
            placeholder,
            keyboard_texture,
            globals_buffer,
            common_src: String::new(),
            channels: std::array::from_fn(|_| {
                std::array::from_fn(|_| ChannelInput::Empty)
            }),
            pass_sources: std::array::from_fn(|_| None),
            pipelines: [None, None, None, None, None],
            pass_dialects: std::array::from_fn(|_| None),
            custom_uniform_buffers: std::array::from_fn(|_| Vec::new()),
            pending_readback: None,
        })
    }

    /// RM10.md section 1, item 8: the real, adapter-reported per-axis
    /// texture size limit on this machine (see `Engine::new`) -- callers
    /// (Python, `lib.rs::Engine::max_texture_dimension`) should validate a
    /// requested resolution against this *before* calling `resize`/
    /// `set_ichannel_texture`/etc. with it, rather than letting an
    /// oversized request reach `wgpu` and panic on a validation error.
    pub fn max_texture_dimension(&self) -> u32 {
        self.max_texture_dimension
    }

    /// Reallocates the output texture and all 4 buffer ping-pong targets
    /// at a new resolution (e.g. the viewport widget was resized).
    /// Compiled pipelines stay valid (the pixel format doesn't change);
    /// buffer contents are cleared to transparent black, same as at
    /// startup, since the old contents don't have a meaningful resized
    /// equivalent.
    /// RM10.md section 1, item 8. A resolution can be within
    /// `max_texture_dimension()` (an adapter-reported *per-axis* limit,
    /// see `Engine::new`) and still exceed available VRAM once the actual
    /// bytes are allocated -- verified: `resize(20000, 20000)` on a real
    /// desktop GPU here fails with wgpu's own "Not enough memory left" in
    /// `Queue::write_texture`, *not* a dimension-limit validation error,
    /// despite 20000 being under this machine's reported
    /// `max_texture_dimension_2d` (32768). Without the error-scope pair
    /// below, that error has no registered handler and reaches wgpu's
    /// default *panicking* uncaptured-error handler instead of coming back
    /// as a normal `Result` -- confirmed by reproducing the panic before
    /// this fix. `push_error_scope`/`pop_error_scope` intercept exactly
    /// that: while a scope is active, a matching error is captured instead
    /// of reaching the uncaptured handler.
    pub fn resize(&mut self, width: u32, height: u32) -> Result<(), String> {
        if width == self.width && height == self.height {
            return Ok(());
        }
        self.device.push_error_scope(wgpu::ErrorFilter::OutOfMemory);
        self.device.push_error_scope(wgpu::ErrorFilter::Validation);
        let (output_texture, output_view) = create_output_texture(&self.device, width, height);
        let buffers = [
            PingPongTarget::new(&self.device, &self.queue, width, height),
            PingPongTarget::new(&self.device, &self.queue, width, height),
            PingPongTarget::new(&self.device, &self.queue, width, height),
            PingPongTarget::new(&self.device, &self.queue, width, height),
        ];
        // Popped in reverse push order (LIFO, as `wgpu` requires): the
        // narrower `Validation` scope first, then `OutOfMemory`. Either
        // one finding an error is reported the same way to the caller --
        // the distinction only matters to `wgpu` internally.
        let validation_err = pollster::block_on(self.device.pop_error_scope());
        let oom_err = pollster::block_on(self.device.pop_error_scope());
        if let Some(e) = validation_err.or(oom_err) {
            return Err(format!(
                "impossible de redimensionner à {width}x{height} : mémoire graphique insuffisante ({e})"
            ));
        }
        // Only committed once the allocation above is confirmed to have
        // actually succeeded -- on error, `self` is left exactly as it was
        // before this call (still at the old, working resolution) rather
        // than half-updated.
        self.width = width;
        self.height = height;
        // Any in-flight readback was sized for the old resolution; its
        // buffer layout no longer matches and its pixels belong to a frame
        // whose viewport size the caller has already discarded, so it must
        // never be resolved.
        self.pending_readback = None;
        self.output_texture = output_texture;
        self.output_view = output_view;
        self.buffers = buffers;
        Ok(())
    }

    pub fn set_common(&mut self, source: &str) {
        self.common_src = source.to_string();
    }

    /// RM10.md section 4 : vide/désactive proprement un onglet de passe —
    /// en pratique un Buffer A-D jamais réellement utilisé par le
    /// projet, que l'appelant Python vient de réduire à une source vide
    /// (voir `main_window.py::_compile_one_pass`, qui appelle ceci plutôt
    /// que de simplement ne plus recompiler). Avant l'ajout de cette
    /// méthode, il n'existait aucun moyen de faire redescendre
    /// `self.pipelines[pass]` à `None` une fois une compilation réussie :
    /// vider le texte de l'éditeur arrêtait bien les recompilations
    /// (`_compile_one_pass` sort tôt sur une source vide), mais le
    /// *dernier* pipeline compilé avec succès restait indéfiniment actif
    /// et continuait d'être rendu à chaque frame dans `submit_frame` —
    /// un Buffer qu'on croyait avoir « vidé » continuait donc de coûter
    /// du temps de rendu, exactement ce que cet item interdit. Une fois
    /// `self.pipelines[pass]` remis à `None`, `submit_frame` retrouve son
    /// comportement normal pour une passe jamais compilée : la boucle
    /// `if self.pipelines[buf_idx].is_none() { continue; }` la saute
    /// entièrement, sans aucun coût de rendu résiduel. Le contenu de sa
    /// texture de lecture (visible par toute autre passe qui la
    /// référence encore via `iChannel`) reste figé sur sa dernière image
    /// rendue plutôt que d'être effacé — un choix délibéré : disparaître
    /// brutalement au premier flip serait plus surprenant qu'utile pour
    /// une passe qu'on est peut-être seulement en train de retravailler.
    ///
    /// N'importe quel `pass` valide est accepté (pas seulement les
    /// buffers) : `render()` gère déjà proprement `PASS_IMAGE` sans
    /// pipeline (`"aucun shader compilé avec succès pour le moment"`,
    /// une `Err` normale, jamais un panic) si un appelant choisissait un
    /// jour de vider Image aussi.
    pub fn clear_pass(&mut self, pass: usize) -> Result<(), String> {
        if pass >= NUM_PASSES {
            return Err(format!("pass invalide: {pass}"));
        }
        self.pipelines[pass] = None;
        self.pass_dialects[pass] = None;
        Ok(())
    }

    /// Compiles one pass (`PASS_BUFFER_A`..`PASS_BUFFER_D` or `PASS_IMAGE`).
    /// The `Common` source (see `set_common`) is prepended first, exactly
    /// like Shadertoy's own "Common" tab — for *both* dialects below: a
    /// GLSL standalone pass can still call Common-declared helpers, Common
    /// itself staying visible but inert w.r.t. dialect detection (it has
    /// neither `mainImage` nor `main()` of its own, see `dialect.rs`).
    ///
    /// Which of the two compilation paths runs is decided here, once, from
    /// `dialect::detect_dialect` on the combined source — never
    /// re-detected independently elsewhere in this file, so the pipeline
    /// that actually gets built always agrees with `self.pass_dialects`
    /// (itself the same value `main_window.py`'s footer indicator would
    /// compute for this same text, see `pass_dialects`'s doc comment):
    /// - `Shadertoy` : chemin historique inchangé (`build_fragment_source`),
    ///   wrapper `mainImage` fixe.
    /// - `GlslStandalone` : `build_fragment_source_standalone`, harness
    ///   conditionnelle — voir sa doc pour le détail de ce qui est injecté
    ///   ou non.
    ///
    /// Le choix entre les deux ne passe plus par un `match` en dur ici :
    /// `shader::compile_backend_for` cherche le backend associé au
    /// dialecte détecté dans un petit registre (roadmap1.md, section
    /// "Architecture extensible pour de futurs langages") — un futur
    /// troisième dialecte avec un backend fonctionnel n'imposera aucune
    /// modification de cette fonction, juste une nouvelle entrée dans ce
    /// registre (voir `ARCHITECTURE.md`).
    pub fn compile_pass(&mut self, pass: usize, user_src: &str) -> Result<(), String> {
        if pass >= NUM_PASSES {
            return Err(format!("pass invalide: {pass}"));
        }
        let combined = if self.common_src.trim().is_empty() {
            user_src.to_string()
        } else {
            format!("{}\n{}", self.common_src, user_src)
        };
        let channel_kinds: [shader::ChannelKind; 4] =
            std::array::from_fn(|i| Self::channel_kind(&self.channels[pass][i]));

        let detection = dialect::detect_dialect(&combined, self.pass_dialects[pass]);
        // Mis à jour immédiatement (avant même de tenter la compilation
        // GLSL) : c'est une propriété du *texte*, pas du succès de sa
        // compilation — un shader qui ne compile pas encore garde quand
        // même son dialecte détecté comme "previous" pour la prochaine
        // frappe, exactement comme le ferait l'indicateur du footer côté
        // Python pour ce même texte.
        self.pass_dialects[pass] = Some(detection.dialect);
        let backend = shader::compile_backend_for(detection.dialect);
        let (fragment_src, custom_uniforms) = backend(&combined, channel_kinds, pass == PASS_IMAGE);

        // RMLG.md, section 1.3/1.5 : `wgpu::ShaderSource::Wgsl` existe
        // depuis toujours comme variante de cet enum (`wgpu::ShaderSource`)
        // à côté de `::Glsl`, mais rien ici ne branchait jamais dessus tant
        // qu'aucun test ne soumettait *réellement* un pass WGSL au device
        // -- `cargo test --lib` ne l'aurait jamais détecté, ces tests
        // n'exerçant que la génération de texte de `shader.rs`, jamais
        // `Engine::compile_pass` lui-même. Trouvé et corrigé en écrivant
        // le scénario de rendu réel de la section 1.5
        // (`test_dialect_detection.py`, scénario 4) : sans ce branchement,
        // tout code WGSL passthrough (texte par ailleurs correct) était
        // soumis au frontend `naga` **GLSL**, qui échoue immédiatement sur
        // `@fragment` (`UnexpectedCharacter`) -- une regression que
        // `compile_backends_cover_every_known_dialect`/les tests texte de
        // `shader::wgsl_tests` ne pouvaient pas voir puisqu'aucun des deux
        // n'appelle jamais `device.create_shader_module`.
        let fragment_source = if detection.dialect == ShaderDialect::Wgsl {
            wgpu::ShaderSource::Wgsl(fragment_src.clone().into())
        } else {
            // GLSL only (never WGSL, whose constructors have no such
            // "extra arguments ignored" rule to begin with): rewrites a
            // handful of `matN(...)` constructor calls naga's GLSL
            // frontend can't parse despite being legal GLSL -- see
            // `ctor_fixup`'s module docs for the exact hazard and why
            // this is safe to apply unconditionally here.
            let fixed_src = crate::ctor_fixup::fixup_overloaded_matrix_constructors(&fragment_src);
            wgpu::ShaderSource::Glsl {
                shader: fixed_src.into(),
                stage: wgpu::naga::ShaderStage::Fragment,
                defines: Default::default(),
            }
        };
        // Même remarque pour le nom du point d'entrée : le harness GLSL
        // (Shadertoy/standalone) construit toujours lui-même un
        // `void main()` fixe (voir `shader::build_fragment_source*`), mais
        // WGSL passthrough ne réécrit jamais le code utilisateur -- son
        // point d'entrée réel peut porter n'importe quel nom (voir
        // `dialect::wgsl_fragment_entry_point_name`, déjà présente et
        // testée côté Rust, mais jamais encore appelée depuis ici avant ce
        // correctif). Un `unwrap_or("main")` reste un filet de sécurité
        // raisonnable : ne devrait jamais être pris pour un pass dont le
        // dialecte détecté est déjà `Wgsl`, `matches_wgsl_entry_point`
        // (dialect.rs) cherchant exactement le même motif que cette
        // fonction pour classer le dialecte en premier lieu.
        let fragment_entry_point: String = if detection.dialect == ShaderDialect::Wgsl {
            let stripped_fragment = dialect::strip_comments(&fragment_src);
            dialect::wgsl_fragment_entry_point_name(&stripped_fragment).unwrap_or_else(|| "main".to_string())
        } else {
            "main".to_string()
        };

        self.device.push_error_scope(wgpu::ErrorFilter::Validation);
        let vertex_module = self.device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("fullscreen-vertex"),
            source: wgpu::ShaderSource::Glsl {
                shader: shader::VERTEX_SRC.into(),
                stage: wgpu::naga::ShaderStage::Vertex,
                defines: Default::default(),
            },
        });
        let fragment_module = self.device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("user-fragment"),
            source: fragment_source,
        });
        let error = pollster::block_on(self.device.pop_error_scope());
        if let Some(err) = error {
            return Err(format!("erreur de compilation du shader:\n{err}"));
        }

        // Rebuilt every compile (cheap: a handful of descriptor structs,
        // no GPU work) so the bind group layout always matches exactly
        // what this compile's shader declares for each iChannel slot —
        // see `channel_binding_entry`.
        let mut entries = vec![wgpu::BindGroupLayoutEntry {
            binding: 0,
            visibility: wgpu::ShaderStages::FRAGMENT,
            ty: wgpu::BindingType::Buffer {
                ty: wgpu::BufferBindingType::Uniform,
                has_dynamic_offset: false,
                min_binding_size: None,
            },
            count: None,
        }];
        entries.extend(channel_binding_entry(1, 2, channel_kinds[0]));
        entries.extend(channel_binding_entry(3, 4, channel_kinds[1]));
        entries.extend(channel_binding_entry(5, 6, channel_kinds[2]));
        entries.extend(channel_binding_entry(7, 8, channel_kinds[3]));
        // Un binding par `uniform` personnalisé auto-détecté en mode GLSL
        // standalone (voir `shader::CustomUniformDecl`) — vide pour le
        // chemin Shadertoy et pour un standalone qui n'en déclare aucun.
        for decl in &custom_uniforms {
            entries.push(wgpu::BindGroupLayoutEntry {
                binding: decl.binding,
                visibility: wgpu::ShaderStages::FRAGMENT,
                ty: wgpu::BindingType::Buffer {
                    ty: wgpu::BufferBindingType::Uniform,
                    has_dynamic_offset: false,
                    min_binding_size: None,
                },
                count: None,
            });
        }
        let bind_group_layout = self.device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("shadertoy-bind-group-layout"),
            entries: &entries,
        });
        let pipeline_layout = self.device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("shadertoy-pipeline-layout"),
            bind_group_layouts: &[&bind_group_layout],
            push_constant_ranges: &[],
        });

        // Buffer A-D render into BUFFER_FORMAT ping-pong targets; only the
        // Image pass renders into the 8-bit output texture. The pipeline's
        // color target format must match whichever it actually writes to
        // (wgpu validates render-pass attachment format against it), so it
        // can't just reuse OUTPUT_FORMAT for every pass anymore.
        let target_format = if pass < NUM_BUFFERS { BUFFER_FORMAT } else { OUTPUT_FORMAT };
        let pipeline = self.device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("shadertoy-pipeline"),
            layout: Some(&pipeline_layout),
            vertex: wgpu::VertexState {
                module: &vertex_module,
                entry_point: "main",
                buffers: &[],
                compilation_options: Default::default(),
            },
            fragment: Some(wgpu::FragmentState {
                module: &fragment_module,
                entry_point: &fragment_entry_point,
                targets: &[Some(wgpu::ColorTargetState {
                    format: target_format,
                    blend: None,
                    write_mask: wgpu::ColorWrites::ALL,
                })],
                compilation_options: Default::default(),
            }),
            primitive: wgpu::PrimitiveState::default(),
            depth_stencil: None,
            multisample: wgpu::MultisampleState::default(),
            multiview: None,
        });

        // Un petit UBO zero-fill par uniform personnalisé (16 octets fixes
        // quel que soit le type — `float`/`int`/`bool` gaspillent un peu
        // par rapport au strict std140, mais évite toute logique de taille
        // par type pour un cas volontairement simple, voir
        // `CustomUniformDecl`). Valeur par défaut 0 dans tous les cas : le
        // shader compile et s'affiche, potentiellement "cassé"
        // visuellement si l'utilisateur attendait une vraie valeur —
        // choix documenté dans `shader::CustomUniformDecl`.
        let custom_buffers: Vec<(u32, wgpu::Buffer)> = custom_uniforms
            .iter()
            .map(|decl| {
                let buffer = self.device.create_buffer(&wgpu::BufferDescriptor {
                    label: Some(&format!("custom-uniform-{}", decl.name)),
                    size: 16,
                    usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
                    mapped_at_creation: true,
                });
                {
                    let mut view = buffer.slice(..).get_mapped_range_mut();
                    view.fill(0);
                }
                buffer.unmap();
                (decl.binding, buffer)
            })
            .collect();

        self.pipelines[pass] = Some(pipeline);
        self.bind_group_layouts[pass] = Some(bind_group_layout);
        self.pipeline_layouts[pass] = Some(pipeline_layout);
        self.pass_sources[pass] = Some(user_src.to_string());
        self.custom_uniform_buffers[pass] = custom_buffers;
        Ok(())
    }

    /// Exporte un pass déjà compilé avec succès vers du texte HLSL ou MSL
    /// (RMLG.md, section "2. HLSL & MSL — cibles d'export", 2.2). Réutilise
    /// exactement les mêmes ingrédients que le dernier `compile_pass`
    /// réussi pour ce pass -- `common_src` + `pass_sources[pass]` (même
    /// concaténation qu'en tête de `compile_pass`), le dialecte détecté
    /// alors (`pass_dialects[pass]`), et les `ChannelKind` actuels de ses
    /// iChannel0-3 (`channels[pass]`, via `channel_kind`) -- c'est-à-dire
    /// exactement ce que `compile_pass` passerait aujourd'hui au même
    /// backend pour ce même pass. Erreur explicite si ce pass n'a encore
    /// jamais compilé avec succès (`pass_sources[pass]` n'est peuplé
    /// qu'après la création réussie du pipeline, voir la fin de
    /// `compile_pass`) plutôt que d'exporter un texte obsolète ou vide.
    pub fn export_shader_as(&self, pass: usize, target: shader::ExportTarget) -> Result<String, String> {
        if pass >= NUM_PASSES {
            return Err(format!("pass invalide: {pass}"));
        }
        let user_src = self.pass_sources[pass]
            .as_ref()
            .ok_or_else(|| "ce pass n'a pas encore été compilé avec succès".to_string())?;
        let dialect = self.pass_dialects[pass]
            .ok_or_else(|| "dialecte inconnu pour ce pass (aucune compilation réussie)".to_string())?;
        let combined = if self.common_src.trim().is_empty() {
            user_src.clone()
        } else {
            format!("{}\n{}", self.common_src, user_src)
        };
        let channel_kinds: [shader::ChannelKind; 4] =
            std::array::from_fn(|i| Self::channel_kind(&self.channels[pass][i]));
        shader::export_shader_as(&combined, dialect, target, channel_kinds)
    }

    fn check_pass_channel(pass: usize, index: u32) -> Result<(), String> {
        if pass >= NUM_PASSES {
            return Err(format!("pass invalide: {pass}"));
        }
        if index > 3 {
            return Err("index iChannel invalide (0-3 attendu)".to_string());
        }
        Ok(())
    }

    /// Which GLSL sampler type (see `shader::ChannelKind`) a given channel
    /// input needs. Only `Cubemap` differs from plain 2D.
    fn channel_kind(input: &ChannelInput) -> shader::ChannelKind {
        match input {
            ChannelInput::Cubemap(_) => shader::ChannelKind::Cube,
            ChannelInput::Empty
            | ChannelInput::Image(_)
            | ChannelInput::Procedural(_)
            | ChannelInput::Buffer(_)
            | ChannelInput::Keyboard
            | ChannelInput::Video(_, _)
            | ChannelInput::Audio(_, _) => shader::ChannelKind::D2,
        }
    }

    /// Assigns `new_input` to a pass's iChannel slot. If this changes that
    /// slot's `ChannelKind` (i.e. a cubemap is assigned or unassigned —
    /// every other kind change is still plain 2D and needs no shader
    /// change), the pass is silently recompiled from its last known-good
    /// source so the shader's `sampler2D`/`samplerCube` declaration and the
    /// bind group layout stay in sync with what's actually bound. This is
    /// what lets every `set_ichannel_*`/`clear_ichannel` call remain a
    /// single call from the Python side, exactly like before cubemaps
    /// existed — no separate `compile_pass` call needed after reassigning
    /// a channel.
    fn set_channel_input(&mut self, pass: usize, index: u32, new_input: ChannelInput) -> Result<(), String> {
        let idx = index as usize;
        let old_kind = Self::channel_kind(&self.channels[pass][idx]);
        self.channels[pass][idx] = new_input;
        let new_kind = Self::channel_kind(&self.channels[pass][idx]);
        if old_kind != new_kind {
            if let Some(src) = self.pass_sources[pass].clone() {
                self.compile_pass(pass, &src)?;
            }
        }
        Ok(())
    }

    pub fn set_ichannel_texture(&mut self, pass: usize, index: u32, path: &str) -> Result<(), String> {
        Self::check_pass_channel(pass, index)?;
        let tex = ChannelTexture::from_file(&self.device, &self.queue, path)?;
        self.set_channel_input(pass, index, ChannelInput::Image(tex))
    }

    /// Points a pass's iChannel slot at a built-in procedural texture
    /// preset (`"checker"`, `"white_noise"`, `"value_noise"`), generated on
    /// the CPU and uploaded once — no image file required, matching
    /// Shadertoy's own preset texture picker. `scale`/`seed` -- see
    /// `ChannelTexture::procedural`'s doc comment.
    pub fn set_ichannel_procedural(
        &mut self, pass: usize, index: u32, kind: &str, scale: u32, seed: u32,
    ) -> Result<(), String> {
        Self::check_pass_channel(pass, index)?;
        let kind = ProceduralKind::from_str(kind)?;
        let tex = ChannelTexture::procedural(&self.device, &self.queue, kind, scale, seed);
        self.set_channel_input(pass, index, ChannelInput::Procedural(tex))
    }

    /// Points a pass's iChannel slot at one of the 4 Buffer targets
    /// (`PASS_BUFFER_A`..`PASS_BUFFER_D`), matching Shadertoy's
    /// "Buffer A/B/C/D" iChannel source options.
    pub fn set_ichannel_buffer(&mut self, pass: usize, index: u32, buffer: usize) -> Result<(), String> {
        Self::check_pass_channel(pass, index)?;
        if buffer >= NUM_BUFFERS {
            return Err(format!("buffer invalide: {buffer}"));
        }
        self.set_channel_input(pass, index, ChannelInput::Buffer(buffer))
    }

    /// Points a pass's iChannel slot at a 6-face cubemap, matching
    /// Shadertoy's cubemap iChannel source option. `paths` must be the 6
    /// face image paths in `+X, -X, +Y, -Y, +Z, -Z` order (see
    /// `ChannelTexture::from_cubemap_files`).
    pub fn set_ichannel_cubemap(&mut self, pass: usize, index: u32, paths: &[String]) -> Result<(), String> {
        Self::check_pass_channel(pass, index)?;
        let tex = ChannelTexture::from_cubemap_files(&self.device, &self.queue, paths)?;
        self.set_channel_input(pass, index, ChannelInput::Cubemap(tex))
    }

    /// Points a pass's iChannel slot at the shared `iKeyboard` texture,
    /// matching Shadertoy's "Keyboard" iChannel source option. No
    /// path/kind argument needed — there's only one keyboard, fed by
    /// `update_keyboard`.
    pub fn set_ichannel_keyboard(&mut self, pass: usize, index: u32) -> Result<(), String> {
        Self::check_pass_channel(pass, index)?;
        self.set_channel_input(pass, index, ChannelInput::Keyboard)
    }

    /// Points a pass's iChannel slot at a video file or webcam, matching
    /// Shadertoy's "Video" / "Webcam" iChannel source options. The engine
    /// does no decoding itself: this only allocates a 1x1 placeholder
    /// texture so the slot has something valid bound right away; the
    /// Python side opens the file/camera with Qt and streams every decoded
    /// frame in via `update_ichannel_video_frame`, exactly like a live
    /// image reload every tick.
    pub fn set_ichannel_video(&mut self, pass: usize, index: u32) -> Result<(), String> {
        Self::check_pass_channel(pass, index)?;
        let tex = ChannelTexture::dynamic(&self.device, &self.queue);
        self.set_channel_input(pass, index, ChannelInput::Video(tex, 0.0))
    }

    /// Uploads one already-decoded video/webcam frame to a pass's iChannel
    /// slot. `rgba` must be tightly packed RGBA8 (`4 * width * height`
    /// bytes, row-major, no row padding). `time` is that source's own
    /// playback position in seconds, exposed to the shader as
    /// `iChannelTime[index]`. A no-op (not an error) if this slot has
    /// since been reassigned away from a video/webcam source — the Python
    /// side stops decoding as soon as it reassigns a slot, but a frame
    /// already queued on the Qt event loop can still land here a tick
    /// later, and that's expected rather than exceptional.
    pub fn update_ichannel_video_frame(
        &mut self,
        pass: usize,
        index: u32,
        width: u32,
        height: u32,
        rgba: &[u8],
        time: f32,
    ) -> Result<(), String> {
        Self::check_pass_channel(pass, index)?;
        let idx = index as usize;
        if let ChannelInput::Video(tex, stored_time) = &mut self.channels[pass][idx] {
            tex.write_rgba(&self.device, &self.queue, width, height, rgba)?;
            *stored_time = time;
        }
        Ok(())
    }

    /// Points a pass's iChannel slot at an audio file, matching
    /// Shadertoy's "Music"/audio iChannel source option. Like
    /// `set_ichannel_video`, the engine does no decoding or FFT itself:
    /// this only allocates the fixed 512x2 texture (zero-filled, i.e.
    /// silence) so the slot has something valid bound right away; the
    /// Python side decodes the file with Qt, computes the spectrum and
    /// waveform, and streams both in every tick via
    /// `update_ichannel_audio_frame`.
    pub fn set_ichannel_audio(&mut self, pass: usize, index: u32) -> Result<(), String> {
        Self::check_pass_channel(pass, index)?;
        let tex = ChannelTexture::audio(&self.device, &self.queue);
        self.set_channel_input(pass, index, ChannelInput::Audio(tex, 0.0))
    }

    /// Uploads one already-computed spectrum/waveform frame to a pass's
    /// iChannel audio slot. `spectrum`/`waveform` are each exactly 512
    /// bytes (see `texture::ChannelTexture::write_audio`). `time` is that
    /// source's own playback position in seconds, exposed to the shader
    /// as `iChannelTime[index]` — same convention as
    /// `update_ichannel_video_frame`. A no-op (not an error) if this slot
    /// has since been reassigned away from Audio, for the same reason a
    /// stray in-flight video frame is a no-op there.
    pub fn update_ichannel_audio_frame(
        &mut self,
        pass: usize,
        index: u32,
        spectrum: &[u8; crate::texture::AUDIO_TEXTURE_WIDTH as usize],
        waveform: &[u8; crate::texture::AUDIO_TEXTURE_WIDTH as usize],
        time: f32,
    ) -> Result<(), String> {
        Self::check_pass_channel(pass, index)?;
        let idx = index as usize;
        if let ChannelInput::Audio(tex, stored_time) = &mut self.channels[pass][idx] {
            tex.write_audio(&self.queue, spectrum, waveform)?;
            *stored_time = time;
        }
        Ok(())
    }

    pub fn clear_ichannel(&mut self, pass: usize, index: u32) -> Result<(), String> {
        Self::check_pass_channel(pass, index)?;
        self.set_channel_input(pass, index, ChannelInput::Empty)
    }

    /// Re-uploads the shared `iKeyboard` texture from the UI's current
    /// keyboard state: three flat 256-entry byte arrays (one entry per JS-style
    /// legacy `keyCode`, `0`/non-zero), one per Shadertoy keyboard-texture
    /// row (down, pressed-this-frame, toggled — see
    /// `texture::ChannelTexture::write_keyboard_state`). Cheap to call
    /// every frame regardless of whether any pass actually has a Keyboard
    /// channel assigned (same as `write_globals` already does
    /// unconditionally for iMouse/iDate/etc.).
    pub fn update_keyboard(&mut self, down: &[u8], pressed: &[u8], toggled: &[u8]) -> Result<(), String> {
        self.keyboard_texture.write_keyboard_state(&self.queue, down, pressed, toggled)
    }

    fn resolve_view<'a>(&'a self, input: &'a ChannelInput) -> &'a wgpu::TextureView {
        match input {
            ChannelInput::Empty => &self.placeholder.view,
            ChannelInput::Image(tex) | ChannelInput::Procedural(tex) | ChannelInput::Cubemap(tex) => &tex.view,
            // Always sample whichever texture holds the most recently
            // completed frame of that buffer: for a pass rendered *after*
            // this buffer in the A→B→C→D→Image order, that's this frame's
            // fresh result (forward reference); for a pass rendered
            // *before* it (backward reference) or the buffer reading
            // itself, that's last frame's result. No special-casing
            // needed — `latest` already encodes exactly that.
            ChannelInput::Buffer(i) => self.buffers[*i].latest_view(),
            ChannelInput::Keyboard => &self.keyboard_texture.view,
            ChannelInput::Video(tex, _) | ChannelInput::Audio(tex, _) => &tex.view,
        }
    }

    fn resolve_size(&self, input: &ChannelInput) -> (u32, u32) {
        match input {
            ChannelInput::Empty => (1, 1),
            ChannelInput::Image(tex) | ChannelInput::Procedural(tex) | ChannelInput::Cubemap(tex) => {
                // For a cubemap this is one face's resolution (`texture.size()`
                // ignores `depth_or_array_layers`), matching what
                // `iChannelResolution` should report for a cube sampler.
                let size = tex.texture.size();
                (size.width, size.height)
            }
            ChannelInput::Buffer(_) => (self.width, self.height),
            ChannelInput::Keyboard => (crate::texture::KEYBOARD_WIDTH, crate::texture::KEYBOARD_ROWS),
            // Whatever the most recently uploaded frame's resolution was
            // (1x1 until the first one arrives) — same `texture.size()`
            // read as the Image/Procedural/Cubemap case above. `Audio` is
            // always 512x2 (see `texture::ChannelTexture::audio`), so this
            // same read just naturally reports that fixed size too.
            ChannelInput::Video(tex, _) | ChannelInput::Audio(tex, _) => {
                let size = tex.texture.size();
                (size.width, size.height)
            }
        }
    }

    fn build_bind_group(&self, pass: usize) -> wgpu::BindGroup {
        let ch = &self.channels[pass];
        let mut entries = vec![
            wgpu::BindGroupEntry {
                binding: 0,
                resource: self.globals_buffer.as_entire_binding(),
            },
            wgpu::BindGroupEntry {
                binding: 1,
                resource: wgpu::BindingResource::TextureView(self.resolve_view(&ch[0])),
            },
            wgpu::BindGroupEntry {
                binding: 2,
                resource: wgpu::BindingResource::Sampler(&self.sampler),
            },
            wgpu::BindGroupEntry {
                binding: 3,
                resource: wgpu::BindingResource::TextureView(self.resolve_view(&ch[1])),
            },
            wgpu::BindGroupEntry {
                binding: 4,
                resource: wgpu::BindingResource::Sampler(&self.sampler),
            },
            wgpu::BindGroupEntry {
                binding: 5,
                resource: wgpu::BindingResource::TextureView(self.resolve_view(&ch[2])),
            },
            wgpu::BindGroupEntry {
                binding: 6,
                resource: wgpu::BindingResource::Sampler(&self.sampler),
            },
            wgpu::BindGroupEntry {
                binding: 7,
                resource: wgpu::BindingResource::TextureView(self.resolve_view(&ch[3])),
            },
            wgpu::BindGroupEntry {
                binding: 8,
                resource: wgpu::BindingResource::Sampler(&self.sampler),
            },
        ];
        // Bindings des `uniform` personnalisés (mode GLSL standalone, voir
        // `compile_pass`/`shader::CustomUniformDecl`) — vide pour toute
        // pass Shadertoy ou standalone n'en déclarant aucun, auquel cas
        // cette boucle ne fait rien et `entries` reste inchangé.
        for (binding, buffer) in &self.custom_uniform_buffers[pass] {
            entries.push(wgpu::BindGroupEntry {
                binding: *binding,
                resource: buffer.as_entire_binding(),
            });
        }
        self.device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("shadertoy-bind-group"),
            // Always `Some` here: `build_bind_group` is only ever called
            // (from `render()`) for a pass whose `pipelines[pass]` is
            // `Some`, and both are set together at the end of a successful
            // `compile_pass`.
            layout: self.bind_group_layouts[pass].as_ref().unwrap(),
            entries: &entries,
        })
    }

    fn write_globals(&self, time: f32, time_delta: f32, mouse: (f32, f32, f32, f32), frame: u32, date: (f32, f32, f32, f32), pass: usize) {
        let mut channel_resolution = [[0f32; 4]; 4];
        let mut channel_time = [[0f32; 4]; 4];
        for (i, input) in self.channels[pass].iter().enumerate() {
            let (w, h) = self.resolve_size(input);
            channel_resolution[i] = [w as f32, h as f32, 1.0, 0.0];
            // Every other channel kind keeps iChannelTime at 0, matching
            // Shadertoy itself (only Video/Webcam/Audio-bound slots ever
            // report a real playback position there).
            if let ChannelInput::Video(_, t) | ChannelInput::Audio(_, t) = input {
                channel_time[i] = [*t, 0.0, 0.0, 0.0];
            }
        }
        let globals = GlobalsUniform {
            resolution: [self.width as f32, self.height as f32, 1.0, 0.0],
            mouse: [mouse.0, mouse.1, mouse.2, mouse.3],
            time,
            time_delta,
            frame: frame as i32,
            _pad0: 0.0,
            date: [date.0, date.1, date.2, date.3],
            sample_rate: DEFAULT_SAMPLE_RATE,
            _pad1: 0.0,
            _pad2: 0.0,
            _pad3: 0.0,
            channel_resolution,
            channel_time,
        };
        self.queue
            .write_buffer(&self.globals_buffer, 0, bytemuck::bytes_of(&globals));
    }

    pub fn render(
        &mut self,
        time: f32,
        time_delta: f32,
        mouse: (f32, f32, f32, f32),
        frame: u32,
        date: (f32, f32, f32, f32),
    ) -> Result<Vec<u8>, String> {
        if self.pipelines[PASS_IMAGE].is_none() {
            return Err("aucun shader compilé avec succès pour le moment".to_string());
        }

        // Bug corrigé (RM.md, Priorité 1 : "premier render() renvoie un
        // buffer entièrement à zéro"). `pending_readback` n'est vide que
        // dans deux cas : tout premier appel après `Engine::new`, ou
        // premier appel après un `resize()` (qui le remet à `None` lui
        // aussi). Dans ces cas-là, `submit_frame` ci-dessous n'aurait rien
        // à renvoyer comme "frame précédente" et on serait forcé de faire
        // fuiter un buffer vide côté Python. On absorbe donc ici, en
        // interne, une frame de chauffe supplémentaire, soumise avec
        // exactement les mêmes paramètres (`time`/`mouse`/`frame`/`date`)
        // que l'appel réel juste en dessous : la "frame précédente" qu'il
        // récupérera sera donc pixel-identique à ce qu'attend l'appelant,
        // sans jamais lui exposer de buffer à zéro. Coût : une soumission
        // GPU en plus, mais seulement lors de ce tout premier appel (ou du
        // premier après resize) — tous les suivants restent aussi pipelinés
        // qu'avant.
        if self.pending_readback.is_none() {
            self.submit_frame(time, time_delta, mouse, frame, date);
        }

        match self.submit_frame(time, time_delta, mouse, frame, date) {
            Some(previous) => Self::resolve_readback(&self.device, previous),
            None => unreachable!(
                "pending_readback vient d'être peuplé juste au-dessus par la frame de chauffe"
            ),
        }
    }

    /// Encode et soumet une frame complète (passes Buffer A-D puis Image),
    /// programme la lecture GPU→CPU du résultat de façon asynchrone, et la
    /// stocke comme "lecture en attente" — en renvoyant celle qui y était
    /// éventuellement déjà (soumise par l'appel précédent). Factorisé hors
    /// de `render()` pour pouvoir être appelé deux fois lors du tout premier
    /// appel (frame de chauffe + frame réelle, voir `render()`) sans dupliquer
    /// tout l'encodage.
    fn submit_frame(
        &mut self,
        time: f32,
        time_delta: f32,
        mouse: (f32, f32, f32, f32),
        frame: u32,
        date: (f32, f32, f32, f32),
    ) -> Option<PendingReadback> {
        let mut encoder = self
            .device
            .create_command_encoder(&wgpu::CommandEncoderDescriptor { label: Some("frame") });

        // Buffer A -> B -> C -> D, each may read any buffer's most recent
        // completed frame (itself included), resolved fresh per pass.
        for buf_idx in 0..NUM_BUFFERS {
            if self.pipelines[buf_idx].is_none() {
                continue;
            }
            self.write_globals(time, time_delta, mouse, frame, date, buf_idx);
            let bind_group = self.build_bind_group(buf_idx);
            {
                let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                    label: Some("buffer-pass"),
                    color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                        view: self.buffers[buf_idx].write_view(),
                        resolve_target: None,
                        ops: wgpu::Operations {
                            load: wgpu::LoadOp::Clear(wgpu::Color::BLACK),
                            store: wgpu::StoreOp::Store,
                        },
                    })],
                    depth_stencil_attachment: None,
                    timestamp_writes: None,
                    occlusion_query_set: None,
                });
                pass.set_pipeline(self.pipelines[buf_idx].as_ref().unwrap());
                pass.set_bind_group(0, &bind_group, &[]);
                pass.draw(0..3, 0..1);
            }
            self.buffers[buf_idx].flip();
        }

        self.write_globals(time, time_delta, mouse, frame, date, PASS_IMAGE);
        let image_bind_group = self.build_bind_group(PASS_IMAGE);
        {
            let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("image-pass"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: &self.output_view,
                    resolve_target: None,
                    ops: wgpu::Operations {
                        load: wgpu::LoadOp::Clear(wgpu::Color::BLACK),
                        store: wgpu::StoreOp::Store,
                    },
                })],
                depth_stencil_attachment: None,
                timestamp_writes: None,
                occlusion_query_set: None,
            });
            pass.set_pipeline(self.pipelines[PASS_IMAGE].as_ref().unwrap());
            pass.set_bind_group(0, &image_bind_group, &[]);
            pass.draw(0..3, 0..1);
        }

        let bytes_per_pixel = 4u32;
        let unpadded_bytes_per_row = self.width * bytes_per_pixel;
        let align = 256u32;
        let padded_bytes_per_row = ((unpadded_bytes_per_row + align - 1) / align) * align;

        let readback_buffer = self.device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("readback"),
            size: (padded_bytes_per_row * self.height) as u64,
            usage: wgpu::BufferUsages::COPY_DST | wgpu::BufferUsages::MAP_READ,
            mapped_at_creation: false,
        });

        encoder.copy_texture_to_buffer(
            wgpu::ImageCopyTexture {
                texture: &self.output_texture,
                mip_level: 0,
                origin: wgpu::Origin3d::ZERO,
                aspect: wgpu::TextureAspect::All,
            },
            wgpu::ImageCopyBuffer {
                buffer: &readback_buffer,
                layout: wgpu::ImageDataLayout {
                    offset: 0,
                    bytes_per_row: Some(padded_bytes_per_row),
                    rows_per_image: Some(self.height),
                },
            },
            wgpu::Extent3d {
                width: self.width,
                height: self.height,
                depth_or_array_layers: 1,
            },
        );

        self.queue.submit(Some(encoder.finish()));

        let (tx, rx) = std::sync::mpsc::channel();
        readback_buffer
            .slice(..)
            .map_async(wgpu::MapMode::Read, move |result| {
                let _ = tx.send(result);
            });
        let current = PendingReadback {
            buffer: readback_buffer,
            receiver: rx,
            width: self.width,
            height: self.height,
            padded_bytes_per_row,
        };

        // Renvoie la lecture *précédente* (soumise par l'appel d'avant),
        // pas celle qu'on vient de programmer : le temps que l'appelant
        // revienne pour une frame de plus (une frame UI plus tard), le GPU
        // a presque toujours déjà terminé et son callback de mapping s'est
        // déjà déclenché, donc le `device.poll` dans `resolve_readback`
        // dégénère en un no-op instantané plutôt qu'un vrai blocage.
        // `current` remplace systématiquement ce qui était en attente,
        // qu'il y ait ou non quelque chose à renvoyer cette fois — c'est ce
        // qui garde le pipeline peuplé à chaque appel après le premier.
        //
        // Le tout premier appel après construction/resize n'a encore
        // aucune frame précédente à rendre (rien n'était en vol avant lui) :
        // cette fonction renvoie alors `None`. C'est `render()`, l'appelant,
        // qui gère ce cas en absorbant une frame de chauffe supplémentaire
        // avant d'appeler `submit_frame` une seconde fois, plutôt que de
        // laisser un buffer vide fuiter côté Python.
        self.pending_readback.replace(current)
    }

    /// Blocks until `pending`'s GPU→CPU copy is mapped, then copies the
    /// (row-padding-stripped) pixels out into a plain `Vec<u8>` and unmaps
    /// the buffer. `Maintain::Poll` is tried first (non-blocking) since the
    /// map has usually already completed by the time this runs; only falls
    /// through to a real `Maintain::Wait` if it genuinely hasn't.
    fn resolve_readback(device: &wgpu::Device, pending: PendingReadback) -> Result<Vec<u8>, String> {
        let PendingReadback { buffer, receiver, width, height, padded_bytes_per_row } = pending;

        device.poll(wgpu::Maintain::Poll);
        let map_result = match receiver.try_recv() {
            Ok(result) => result,
            Err(_) => {
                device.poll(wgpu::Maintain::Wait);
                receiver
                    .recv()
                    .map_err(|e| format!("échec de lecture du framebuffer: {e}"))?
            }
        };
        map_result.map_err(|e| format!("échec du mapping du framebuffer: {e}"))?;

        let unpadded_bytes_per_row = width * 4;
        let data = buffer.slice(..).get_mapped_range();
        let mut pixels = Vec::with_capacity((unpadded_bytes_per_row * height) as usize);
        for row in 0..height {
            let start = (row * padded_bytes_per_row) as usize;
            let end = start + unpadded_bytes_per_row as usize;
            pixels.extend_from_slice(&data[start..end]);
        }
        drop(data);
        buffer.unmap();

        Ok(pixels)
    }
}
