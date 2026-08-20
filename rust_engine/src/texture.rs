pub struct ChannelTexture {
    pub texture: wgpu::Texture,
    pub view: wgpu::TextureView,
}

/// Built-in procedural texture presets, generated on the CPU and uploaded
/// once at assignment time — no image file needed, matching (a small,
/// practical subset of) Shadertoy's own preset texture picker.
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum ProceduralKind {
    Checker,
    WhiteNoise,
    ValueNoise,
}

impl ProceduralKind {
    /// Parses the string identifier shared with the Python binding and the
    /// project `.json` format (an iChannel slot of kind `"procedural"`
    /// stores one of these as its `value`).
    pub fn from_str(s: &str) -> Result<Self, String> {
        match s {
            "checker" => Ok(Self::Checker),
            "white_noise" => Ok(Self::WhiteNoise),
            "value_noise" => Ok(Self::ValueNoise),
            other => Err(format!(
                "preset de texture procédurale inconnu: '{other}' (attendu: checker, white_noise, value_noise)"
            )),
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Checker => "checker",
            Self::WhiteNoise => "white_noise",
            Self::ValueNoise => "value_noise",
        }
    }
}

/// Side length (px) of every generated procedural texture. Fixed and
/// independent of the viewport/buffer resolution — like a loaded image
/// file, a preset is just a plain iChannel source sampled with the
/// existing repeat-wrap, linear-filter sampler.
const PROCEDURAL_TEXTURE_SIZE: u32 = 256;

/// Minimal xorshift32 PRNG: no `rand` dependency needed for a few thousand
/// deterministic bytes generated once per assignment. Each preset uses a
/// fixed seed so the same choice always looks the same, run to run and
/// after a project reload — there's no per-frame animation to preserve.
struct XorShift32(u32);

impl XorShift32 {
    fn new(seed: u32) -> Self {
        // xorshift is undefined at a zero state; any non-zero seed works.
        Self(if seed == 0 { 0x9E3779B9 } else { seed })
    }

    fn next_u32(&mut self) -> u32 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 17;
        x ^= x << 5;
        self.0 = x;
        x
    }

    fn next_byte(&mut self) -> u8 {
        (self.next_u32() >> 24) as u8
    }

    fn next_unit_f32(&mut self) -> f32 {
        (self.next_u32() as f32) / (u32::MAX as f32)
    }
}

/// Flat black/white(ish) checkerboard, `cells`x`cells` cells across the
/// texture (RM10.md section 5: the "taille de motif" knob exposed via
/// `Engine.set_ichannel_procedural`'s `scale` parameter -- more cells
/// means a finer checker pattern).
fn generate_checker(size: u32, cells: u32) -> Vec<u8> {
    let cells = cells.max(1);
    let cell = (size / cells).max(1);
    let mut out = vec![0u8; (size * size * 4) as usize];
    for y in 0..size {
        for x in 0..size {
            let on = ((x / cell) + (y / cell)) % 2 == 0;
            let v: u8 = if on { 235 } else { 35 };
            let idx = ((y * size + x) * 4) as usize;
            out[idx] = v;
            out[idx + 1] = v;
            out[idx + 2] = v;
            out[idx + 3] = 255;
        }
    }
    out
}

/// Independent uniform-random RGB per texel — Shadertoy's "RGBA Noise"
/// preset family.
fn generate_white_noise(size: u32, seed: u32) -> Vec<u8> {
    let mut rng = XorShift32::new(seed);
    let mut out = vec![0u8; (size * size * 4) as usize];
    for px in out.chunks_exact_mut(4) {
        px[0] = rng.next_byte();
        px[1] = rng.next_byte();
        px[2] = rng.next_byte();
        px[3] = 255;
    }
    out
}

/// Classic value noise: a coarse grid of random samples, bilinearly
/// interpolated (with a smoothstep fade, not a linear one, to avoid
/// visible grid-diagonal creases) up to the full texel grid. Grayscale,
/// replicated across RGB.
fn generate_value_noise(size: u32, grid_size: u32, seed: u32) -> Vec<u8> {
    // RM10.md section 5: "taille de motif" -- a finer grid (higher value)
    // means smaller, more numerous noise blobs; a coarser one (lower
    // value) means larger, smoother blobs. `.max(1)` avoids a
    // divide-by-zero below if the caller passes 0.
    let grid_size = grid_size.max(1);
    let mut rng = XorShift32::new(seed);
    let mut grid = vec![0f32; ((grid_size + 1) * (grid_size + 1)) as usize];
    for v in grid.iter_mut() {
        *v = rng.next_unit_f32();
    }
    let sample_grid = |gx: u32, gy: u32| grid[(gy * (grid_size + 1) + gx) as usize];
    let fade = |t: f32| t * t * (3.0 - 2.0 * t);

    let mut out = vec![0u8; (size * size * 4) as usize];
    for y in 0..size {
        for x in 0..size {
            let fx = x as f32 / size as f32 * grid_size as f32;
            let fy = y as f32 / size as f32 * grid_size as f32;
            let x0 = fx.floor() as u32;
            let y0 = fy.floor() as u32;
            let x1 = (x0 + 1).min(grid_size);
            let y1 = (y0 + 1).min(grid_size);
            let tx = fade(fx - x0 as f32);
            let ty = fade(fy - y0 as f32);

            let top = sample_grid(x0, y0) * (1.0 - tx) + sample_grid(x1, y0) * tx;
            let bottom = sample_grid(x0, y1) * (1.0 - tx) + sample_grid(x1, y1) * tx;
            let v = (top * (1.0 - ty) + bottom * ty).clamp(0.0, 1.0);
            let byte = (v * 255.0) as u8;

            let idx = ((y * size + x) * 4) as usize;
            out[idx] = byte;
            out[idx + 1] = byte;
            out[idx + 2] = byte;
            out[idx + 3] = 255;
        }
    }
    out
}

/// Shadertoy's `iKeyboard` layout: 256 columns (one per JS-style legacy
/// `keyCode`, 0-255) by 3 rows — row 0 = "is this key currently held
/// down", row 1 = "was this key pressed *this frame*" (a one-frame pulse,
/// also true on OS key-repeat), row 2 = "toggle" (flips low/high/low each
/// time the key goes down, including on repeat). A shader reads it with
/// `texelFetch(iChannelX, ivec2(keyCode, row), 0).x`, exactly like on
/// shadertoy.com.
pub const KEYBOARD_WIDTH: u32 = 256;
pub const KEYBOARD_ROWS: u32 = 3;

/// Shadertoy's `iChannel` audio texture layout: 512 columns (one FFT
/// band/waveform sample each) by 2 rows — row 0 = frequency spectrum
/// (`texture(iChannelX, vec2(u, 0.25))`), row 1 = waveform
/// (`vec2(u, 0.75)`), `u ∈ [0,1]`. Fixed regardless of the source audio
/// file (see `ChannelTexture::audio`).
pub const AUDIO_TEXTURE_WIDTH: u32 = 512;
pub const AUDIO_TEXTURE_HEIGHT: u32 = 2;

impl ChannelTexture {
    /// Creates the persistent keyboard-state texture, zero-filled (no key
    /// down/pressed/toggled yet). Plain `Rgba8Unorm`/`sampler2D`, same as
    /// every other non-cubemap channel — only the byte content this texture
    /// holds is special, not its binding shape. `COPY_DST` so
    /// `write_keyboard_state` can update it in place every frame without
    /// reallocating.
    pub fn keyboard(device: &wgpu::Device, queue: &wgpu::Queue) -> Self {
        let size = wgpu::Extent3d {
            width: KEYBOARD_WIDTH,
            height: KEYBOARD_ROWS,
            depth_or_array_layers: 1,
        };
        let texture = device.create_texture(&wgpu::TextureDescriptor {
            label: Some("ichannel-keyboard"),
            size,
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: wgpu::TextureFormat::Rgba8Unorm,
            usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
            view_formats: &[],
        });
        let view = texture.create_view(&wgpu::TextureViewDescriptor::default());
        let tex = Self { texture, view };
        // Zero-fill up front (write_keyboard_state is only called once a
        // key event has actually happened) so an unused keyboard channel
        // samples defined "nothing is down/pressed/toggled" data instead
        // of driver-dependent garbage.
        let zeros = vec![0u8; (KEYBOARD_WIDTH * KEYBOARD_ROWS * 4) as usize];
        queue.write_texture(
            wgpu::ImageCopyTexture {
                texture: &tex.texture,
                mip_level: 0,
                origin: wgpu::Origin3d::ZERO,
                aspect: wgpu::TextureAspect::All,
            },
            &zeros,
            wgpu::ImageDataLayout {
                offset: 0,
                bytes_per_row: Some(4 * KEYBOARD_WIDTH),
                rows_per_image: Some(KEYBOARD_ROWS),
            },
            size,
        );
        tex
    }

    /// Re-uploads the 3 keyboard-state rows from flat 256-entry byte
    /// arrays (one entry per key code, `0` or non-zero). Only `.x` (the R
    /// channel) is ever sampled by a shader (see the module doc comment
    /// above), so G/B/A are just set to match R — simplest to reason
    /// about, and free (no extra source data to carry around).
    pub fn write_keyboard_state(
        &self,
        queue: &wgpu::Queue,
        down: &[u8],
        pressed: &[u8],
        toggled: &[u8],
    ) -> Result<(), String> {
        let w = KEYBOARD_WIDTH as usize;
        if down.len() != w || pressed.len() != w || toggled.len() != w {
            return Err(format!(
                "état clavier invalide: 256 entrées attendues par ligne (down={}, pressed={}, toggled={})",
                down.len(), pressed.len(), toggled.len()
            ));
        }
        let mut rgba = vec![0u8; w * KEYBOARD_ROWS as usize * 4];
        let mut fill_row = |row: u32, values: &[u8]| {
            let row_start = (row as usize) * w * 4;
            for (i, &v) in values.iter().enumerate() {
                let byte = if v != 0 { 255 } else { 0 };
                let px = row_start + i * 4;
                rgba[px] = byte;
                rgba[px + 1] = byte;
                rgba[px + 2] = byte;
                rgba[px + 3] = 255;
            }
        };
        fill_row(0, down);
        fill_row(1, pressed);
        fill_row(2, toggled);
        queue.write_texture(
            wgpu::ImageCopyTexture {
                texture: &self.texture,
                mip_level: 0,
                origin: wgpu::Origin3d::ZERO,
                aspect: wgpu::TextureAspect::All,
            },
            &rgba,
            wgpu::ImageDataLayout {
                offset: 0,
                bytes_per_row: Some(4 * KEYBOARD_WIDTH),
                rows_per_image: Some(KEYBOARD_ROWS),
            },
            wgpu::Extent3d {
                width: KEYBOARD_WIDTH,
                height: KEYBOARD_ROWS,
                depth_or_array_layers: 1,
            },
        );
        Ok(())
    }

    /// A 1x1 white placeholder texture, used for iChannel slots that have
    /// no image loaded yet so the bind group is always fully populated.
    pub fn placeholder(device: &wgpu::Device, queue: &wgpu::Queue) -> Self {
        let size = wgpu::Extent3d {
            width: 1,
            height: 1,
            depth_or_array_layers: 1,
        };
        let texture = device.create_texture(&wgpu::TextureDescriptor {
            label: Some("ichannel-placeholder"),
            size,
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: wgpu::TextureFormat::Rgba8Unorm,
            usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
            view_formats: &[],
        });
        queue.write_texture(
            wgpu::ImageCopyTexture {
                texture: &texture,
                mip_level: 0,
                origin: wgpu::Origin3d::ZERO,
                aspect: wgpu::TextureAspect::All,
            },
            &[255u8, 255, 255, 255],
            wgpu::ImageDataLayout {
                offset: 0,
                bytes_per_row: Some(4),
                rows_per_image: Some(1),
            },
            size,
        );
        let view = texture.create_view(&wgpu::TextureViewDescriptor::default());
        Self { texture, view }
    }

    pub fn from_file(
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        path: &str,
    ) -> Result<Self, String> {
        let img = image::open(path).map_err(|e| format!("impossible de charger '{path}': {e}"))?;
        let rgba = img.to_rgba8();
        let (width, height) = rgba.dimensions();
        let size = wgpu::Extent3d {
            width,
            height,
            depth_or_array_layers: 1,
        };
        let texture = device.create_texture(&wgpu::TextureDescriptor {
            label: Some(path),
            size,
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: wgpu::TextureFormat::Rgba8Unorm,
            usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
            view_formats: &[],
        });
        queue.write_texture(
            wgpu::ImageCopyTexture {
                texture: &texture,
                mip_level: 0,
                origin: wgpu::Origin3d::ZERO,
                aspect: wgpu::TextureAspect::All,
            },
            &rgba,
            wgpu::ImageDataLayout {
                offset: 0,
                bytes_per_row: Some(4 * width),
                rows_per_image: Some(height),
            },
            size,
        );
        let view = texture.create_view(&wgpu::TextureViewDescriptor::default());
        Ok(Self { texture, view })
    }

    /// Loads a 6-face cubemap from 6 separate image files and uploads it as
    /// a single 6-layer texture with a `Cube`-dimension view. `paths` must
    /// be given in Shadertoy/WebGPU cubemap face order — `+X, -X, +Y, -Y,
    /// +Z, -Z` — since that's exactly the array-layer order a `Cube` view
    /// interprets its 6 layers as; there's no per-face metadata to reorder
    /// them from. All 6 faces must be square (a non-square cubemap face
    /// isn't meaningful) and share the exact same resolution as each
    /// other, matching every cubemap format in practice (Shadertoy's own
    /// included) and avoiding the ambiguity of what a mismatched face size
    /// would even mean.
    pub fn from_cubemap_files(
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        paths: &[String],
    ) -> Result<Self, String> {
        if paths.len() != 6 {
            return Err(format!(
                "cubemap: 6 chemins attendus (+X,-X,+Y,-Y,+Z,-Z), {} reçu(s)",
                paths.len()
            ));
        }
        let mut faces = Vec::with_capacity(6);
        for path in paths {
            let img =
                image::open(path).map_err(|e| format!("impossible de charger '{path}': {e}"))?;
            faces.push(img.to_rgba8());
        }
        let (width, height) = faces[0].dimensions();
        if width != height {
            return Err(format!(
                "cubemap: chaque face doit être carrée (face +X: {width}x{height})"
            ));
        }
        const FACE_LABELS: [&str; 6] = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"];
        for (i, face) in faces.iter().enumerate() {
            if face.dimensions() != (width, height) {
                let (fw, fh) = face.dimensions();
                return Err(format!(
                    "cubemap: toutes les faces doivent avoir la même taille (face {}: {fw}x{fh}, attendu {width}x{height})",
                    FACE_LABELS[i]
                ));
            }
        }

        let size = wgpu::Extent3d {
            width,
            height,
            depth_or_array_layers: 6,
        };
        let texture = device.create_texture(&wgpu::TextureDescriptor {
            label: Some("ichannel-cubemap"),
            size,
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: wgpu::TextureFormat::Rgba8Unorm,
            usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
            view_formats: &[],
        });
        for (layer, face) in faces.iter().enumerate() {
            queue.write_texture(
                wgpu::ImageCopyTexture {
                    texture: &texture,
                    mip_level: 0,
                    origin: wgpu::Origin3d { x: 0, y: 0, z: layer as u32 },
                    aspect: wgpu::TextureAspect::All,
                },
                face,
                wgpu::ImageDataLayout {
                    offset: 0,
                    bytes_per_row: Some(4 * width),
                    rows_per_image: Some(height),
                },
                wgpu::Extent3d { width, height, depth_or_array_layers: 1 },
            );
        }
        let view = texture.create_view(&wgpu::TextureViewDescriptor {
            label: Some("ichannel-cubemap-view"),
            dimension: Some(wgpu::TextureViewDimension::Cube),
            ..Default::default()
        });
        Ok(Self { texture, view })
    }

    /// Creates a texture meant to be re-uploaded every frame from an
    /// externally decoded source (video file / webcam — see
    /// `renderer::ChannelInput::Video`). Starts at a 1x1 placeholder pixel,
    /// same convention as `placeholder()`, so the slot has something valid
    /// bound the instant it's assigned, before the first real frame has
    /// been decoded and pushed by `write_rgba`.
    pub fn dynamic(device: &wgpu::Device, queue: &wgpu::Queue) -> Self {
        let size = wgpu::Extent3d { width: 1, height: 1, depth_or_array_layers: 1 };
        let texture = device.create_texture(&wgpu::TextureDescriptor {
            label: Some("ichannel-video"),
            size,
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: wgpu::TextureFormat::Rgba8Unorm,
            usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
            view_formats: &[],
        });
        queue.write_texture(
            wgpu::ImageCopyTexture {
                texture: &texture,
                mip_level: 0,
                origin: wgpu::Origin3d::ZERO,
                aspect: wgpu::TextureAspect::All,
            },
            &[0u8, 0, 0, 255],
            wgpu::ImageDataLayout {
                offset: 0,
                bytes_per_row: Some(4),
                rows_per_image: Some(1),
            },
            size,
        );
        let view = texture.create_view(&wgpu::TextureViewDescriptor::default());
        Self { texture, view }
    }

    /// Re-uploads one decoded video/webcam frame. `data` must be tightly
    /// packed RGBA8 (`4 * width * height` bytes, row-major, no row
    /// padding) — exactly what Qt's
    /// `QImage::convertToFormat(QImage::Format_RGBA8888)` gives on the
    /// Python side (`video_source.py`). Recreates the texture (and its
    /// view) whenever `width`/`height` differ from what's currently
    /// allocated: a webcam or a video file can in principle change
    /// resolution mid-stream (a different camera picked, a player
    /// renegotiating format), and wgpu textures have no in-place resize.
    pub fn write_rgba(
        &mut self,
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        width: u32,
        height: u32,
        data: &[u8],
    ) -> Result<(), String> {
        let expected = (width as usize) * (height as usize) * 4;
        if data.len() != expected {
            return Err(format!(
                "frame vidéo: {} octets reçus, {expected} attendus pour {width}x{height} en RGBA8",
                data.len()
            ));
        }
        let current = self.texture.size();
        if current.width != width || current.height != height {
            let size = wgpu::Extent3d { width, height, depth_or_array_layers: 1 };
            self.texture = device.create_texture(&wgpu::TextureDescriptor {
                label: Some("ichannel-video"),
                size,
                mip_level_count: 1,
                sample_count: 1,
                dimension: wgpu::TextureDimension::D2,
                format: wgpu::TextureFormat::Rgba8Unorm,
                usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
                view_formats: &[],
            });
            self.view = self.texture.create_view(&wgpu::TextureViewDescriptor::default());
        }
        queue.write_texture(
            wgpu::ImageCopyTexture {
                texture: &self.texture,
                mip_level: 0,
                origin: wgpu::Origin3d::ZERO,
                aspect: wgpu::TextureAspect::All,
            },
            data,
            wgpu::ImageDataLayout {
                offset: 0,
                bytes_per_row: Some(4 * width),
                rows_per_image: Some(height),
            },
            wgpu::Extent3d { width, height, depth_or_array_layers: 1 },
        );
        Ok(())
    }

    /// Creates the fixed-size `iChannel` audio texture (512×2, see
    /// `renderer::ChannelInput::Audio`): row 0 = frequency spectrum, row 1
    /// = waveform, exactly Shadertoy's own audio-channel texture layout.
    /// Unlike `dynamic()` (video/webcam, whose resolution can change
    /// mid-stream) this size never changes regardless of the source audio
    /// file, so there's no equivalent of `write_rgba`'s recreate-on-resize
    /// path — `write_audio` always re-uploads in place. Zero-filled up
    /// front (silence) so the slot samples defined data before the first
    /// real spectrum/waveform frame has been computed and pushed from the
    /// Python side.
    pub fn audio(device: &wgpu::Device, queue: &wgpu::Queue) -> Self {
        let size = wgpu::Extent3d { width: AUDIO_TEXTURE_WIDTH, height: AUDIO_TEXTURE_HEIGHT, depth_or_array_layers: 1 };
        let texture = device.create_texture(&wgpu::TextureDescriptor {
            label: Some("ichannel-audio"),
            size,
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: wgpu::TextureFormat::Rgba8Unorm,
            usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
            view_formats: &[],
        });
        let zeros = vec![0u8; (AUDIO_TEXTURE_WIDTH * AUDIO_TEXTURE_HEIGHT * 4) as usize];
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
                bytes_per_row: Some(4 * AUDIO_TEXTURE_WIDTH),
                rows_per_image: Some(AUDIO_TEXTURE_HEIGHT),
            },
            size,
        );
        let view = texture.create_view(&wgpu::TextureViewDescriptor::default());
        Self { texture, view }
    }

    /// Re-uploads both rows of the audio channel texture. `spectrum` and
    /// `waveform` are each exactly `AUDIO_TEXTURE_WIDTH` (512) bytes —
    /// already-computed 0-255 magnitudes, one column each, R=G=B like
    /// every other single-channel texture here (`keyboard`,
    /// `write_keyboard_state`) so `.r`/`.x` both work in the shader. The
    /// FFT/decoding itself happens entirely on the Python side
    /// (`audio_source.py`) — this call only ever writes the two rows of
    /// an already-fixed-size texture, never recreates it.
    pub fn write_audio(
        &self,
        queue: &wgpu::Queue,
        spectrum: &[u8; AUDIO_TEXTURE_WIDTH as usize],
        waveform: &[u8; AUDIO_TEXTURE_WIDTH as usize],
    ) -> Result<(), String> {
        let mut rgba = vec![0u8; (AUDIO_TEXTURE_WIDTH * AUDIO_TEXTURE_HEIGHT * 4) as usize];
        let mut fill_row = |row: u32, values: &[u8; AUDIO_TEXTURE_WIDTH as usize]| {
            let row_start = (row as usize) * (AUDIO_TEXTURE_WIDTH as usize) * 4;
            for (i, &v) in values.iter().enumerate() {
                let px = row_start + i * 4;
                rgba[px] = v;
                rgba[px + 1] = v;
                rgba[px + 2] = v;
                rgba[px + 3] = 255;
            }
        };
        fill_row(0, spectrum);
        fill_row(1, waveform);
        queue.write_texture(
            wgpu::ImageCopyTexture {
                texture: &self.texture,
                mip_level: 0,
                origin: wgpu::Origin3d::ZERO,
                aspect: wgpu::TextureAspect::All,
            },
            &rgba,
            wgpu::ImageDataLayout {
                offset: 0,
                bytes_per_row: Some(4 * AUDIO_TEXTURE_WIDTH),
                rows_per_image: Some(AUDIO_TEXTURE_HEIGHT),
            },
            wgpu::Extent3d { width: AUDIO_TEXTURE_WIDTH, height: AUDIO_TEXTURE_HEIGHT, depth_or_array_layers: 1 },
        );
        Ok(())
    }

    /// Generates one of the built-in procedural presets and uploads it as a
    /// plain `Rgba8Unorm` texture, exactly like a loaded image file — same
    /// bind group entry, same repeat-wrap/linear sampler, no engine-side
    /// special case at sample time.
    ///
    /// RM10.md section 5: `scale` (pattern size -- checker cell count /
    /// value-noise grid resolution, ignored by white noise, which has no
    /// notion of pattern size) and `seed` (0 means "use this preset's own
    /// default", not literally seed 0 -- `checker` ignores it too, being
    /// deterministic) are user-adjustable rather than hardcoded, unlike
    /// before this existed. The two defaults below (`0x9E3779B9` for white
    /// noise, `0x1234_5678` for value noise) match this feature's original
    /// fixed seeds exactly, so an existing project that never touches the
    /// new controls keeps rendering identically.
    pub fn procedural(
        device: &wgpu::Device, queue: &wgpu::Queue, kind: ProceduralKind, scale: u32, seed: u32,
    ) -> Self {
        let size = PROCEDURAL_TEXTURE_SIZE;
        let scale = if scale == 0 { 8 } else { scale };
        let rgba = match kind {
            ProceduralKind::Checker => generate_checker(size, scale),
            ProceduralKind::WhiteNoise => {
                generate_white_noise(size, if seed == 0 { 0x9E37_79B9 } else { seed })
            }
            ProceduralKind::ValueNoise => {
                generate_value_noise(size, scale, if seed == 0 { 0x1234_5678 } else { seed })
            }
        };
        let extent = wgpu::Extent3d {
            width: size,
            height: size,
            depth_or_array_layers: 1,
        };
        let texture = device.create_texture(&wgpu::TextureDescriptor {
            label: Some(kind.as_str()),
            size: extent,
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: wgpu::TextureFormat::Rgba8Unorm,
            usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
            view_formats: &[],
        });
        queue.write_texture(
            wgpu::ImageCopyTexture {
                texture: &texture,
                mip_level: 0,
                origin: wgpu::Origin3d::ZERO,
                aspect: wgpu::TextureAspect::All,
            },
            &rgba,
            wgpu::ImageDataLayout {
                offset: 0,
                bytes_per_row: Some(4 * size),
                rows_per_image: Some(size),
            },
            extent,
        );
        let view = texture.create_view(&wgpu::TextureViewDescriptor::default());
        Self { texture, view }
    }
}
