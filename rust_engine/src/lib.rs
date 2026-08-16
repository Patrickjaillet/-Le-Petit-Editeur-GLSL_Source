mod golf;
mod literals;
mod renderer;
mod shader;
mod texture;

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

fn to_py_err(e: String) -> PyErr {
    PyRuntimeError::new_err(e)
}

/// A GLSL float literal detected directly in the shader's own code
/// (no annotation involved), exposed to the UI as a slider candidate.
/// `start`/`end` are character offsets into the source string.
#[pyclass]
struct LiteralSlider {
    #[pyo3(get)]
    start: usize,
    #[pyo3(get)]
    end: usize,
    #[pyo3(get)]
    value: f32,
    #[pyo3(get)]
    min: f32,
    #[pyo3(get)]
    max: f32,
    #[pyo3(get)]
    category: String,
}

impl From<literals::LiteralSlider> for LiteralSlider {
    fn from(l: literals::LiteralSlider) -> Self {
        Self {
            start: l.start,
            end: l.end,
            value: l.value,
            min: l.min,
            max: l.max,
            category: l.category,
        }
    }
}

/// A bare `int` literal detected in the shader's own code, exposed as an
/// integer-stepped slider candidate.
#[pyclass]
struct IntSlider {
    #[pyo3(get)]
    start: usize,
    #[pyo3(get)]
    end: usize,
    #[pyo3(get)]
    value: i32,
    #[pyo3(get)]
    min: i32,
    #[pyo3(get)]
    max: i32,
    #[pyo3(get)]
    category: String,
}

impl From<literals::IntSlider> for IntSlider {
    fn from(l: literals::IntSlider) -> Self {
        Self { start: l.start, end: l.end, value: l.value, min: l.min, max: l.max, category: l.category }
    }
}

/// A `true`/`false` literal detected in the shader's own code, exposed as
/// a toggle slider candidate.
#[pyclass]
struct BoolSlider {
    #[pyo3(get)]
    start: usize,
    #[pyo3(get)]
    end: usize,
    #[pyo3(get)]
    value: bool,
    #[pyo3(get)]
    category: String,
}

impl From<literals::BoolSlider> for BoolSlider {
    fn from(l: literals::BoolSlider) -> Self {
        Self { start: l.start, end: l.end, value: l.value, category: l.category }
    }
}

/// A `vec2(a, b)` / `vec3(a, b, c)` / `vec4(a, b, c, d)` call whose
/// arguments are all plain float literals, grouped into one slider
/// candidate (a color picker for `vec3`/`vec4`, an X/Y pair for `vec2`)
/// instead of `size` separate float sliders. `start`/`end` span the
/// entire call; editing this slider replaces that whole span with a
/// freshly formatted `vecN(...)` call.
#[pyclass]
struct VecSlider {
    #[pyo3(get)]
    start: usize,
    #[pyo3(get)]
    end: usize,
    #[pyo3(get)]
    size: u8,
    #[pyo3(get)]
    values: Vec<f32>,
    #[pyo3(get)]
    category: String,
}

impl From<literals::VecSlider> for VecSlider {
    fn from(l: literals::VecSlider) -> Self {
        Self { start: l.start, end: l.end, size: l.size, values: l.values, category: l.category }
    }
}

#[pyclass(unsendable)]
struct Engine {
    inner: renderer::Engine,
}

#[pymethods]
impl Engine {
    #[new]
    fn new(width: u32, height: u32) -> PyResult<Self> {
        let inner = renderer::Engine::new(width, height).map_err(to_py_err)?;
        Ok(Self { inner })
    }

    /// Sets the `Common` source: plain GLSL prepended to every pass
    /// (Buffer A-D and Image) before compilation, matching Shadertoy's own
    /// "Common" tab. Takes effect on the next `compile_pass` call(s).
    fn set_common(&mut self, source: &str) {
        self.inner.set_common(source);
    }

    /// Compiles one pass's plain, 100% Shadertoy-compatible `mainImage`
    /// fragment source (no custom uniforms/annotations required).
    /// `pass` is one of the `PASS_*` module constants.
    fn compile_pass(&mut self, pass: usize, source: &str) -> PyResult<()> {
        self.inner.compile_pass(pass, source).map_err(to_py_err)
    }

    fn set_ichannel_texture(&mut self, pass: usize, index: u32, path: &str) -> PyResult<()> {
        self.inner.set_ichannel_texture(pass, index, path).map_err(to_py_err)
    }

    /// Points a pass's iChannel slot at a built-in procedural texture
    /// preset (`"checker"`, `"white_noise"`, `"value_noise"`) generated on
    /// the CPU and uploaded once — no image file needed, matching
    /// Shadertoy's own preset texture picker.
    fn set_ichannel_procedural(&mut self, pass: usize, index: u32, kind: &str) -> PyResult<()> {
        self.inner.set_ichannel_procedural(pass, index, kind).map_err(to_py_err)
    }

    /// Points a pass's iChannel slot at one of the 4 Buffer render targets
    /// (0=Buffer A .. 3=Buffer D), matching Shadertoy's "Buffer A/B/C/D"
    /// iChannel source option.
    fn set_ichannel_buffer(&mut self, pass: usize, index: u32, buffer: usize) -> PyResult<()> {
        self.inner.set_ichannel_buffer(pass, index, buffer).map_err(to_py_err)
    }

    /// Points a pass's iChannel slot at a 6-face cubemap (`samplerCube`),
    /// matching Shadertoy's cubemap iChannel source option. `paths` must be
    /// the 6 face image file paths in `+X, -X, +Y, -Y, +Z, -Z` order — the
    /// order a `Cube`-dimension texture view reads its 6 array layers as.
    fn set_ichannel_cubemap(&mut self, pass: usize, index: u32, paths: Vec<String>) -> PyResult<()> {
        self.inner.set_ichannel_cubemap(pass, index, &paths).map_err(to_py_err)
    }

    /// Points a pass's iChannel slot at the shared `iKeyboard` texture
    /// (256x3: row 0 = key down, row 1 = pressed this frame, row 2 =
    /// toggled), matching Shadertoy's "Keyboard" iChannel source option.
    /// Actual key events are pushed separately via `update_keyboard`.
    fn set_ichannel_keyboard(&mut self, pass: usize, index: u32) -> PyResult<()> {
        self.inner.set_ichannel_keyboard(pass, index).map_err(to_py_err)
    }

    /// Points a pass's iChannel slot at a video file or webcam, matching
    /// Shadertoy's "Video" / "Webcam" iChannel source options. Decoding
    /// itself happens entirely on the Python side (Qt's own `QMediaPlayer`
    /// / `QCamera`, see `video_source.py`); this call only allocates a
    /// placeholder texture so the slot has something valid bound
    /// immediately. Actual frames arrive via `update_ichannel_video_frame`.
    fn set_ichannel_video(&mut self, pass: usize, index: u32) -> PyResult<()> {
        self.inner.set_ichannel_video(pass, index).map_err(to_py_err)
    }

    /// Uploads one already-decoded video/webcam frame to a pass's iChannel
    /// slot. `rgba` must be tightly packed RGBA8 (`4 * width * height`
    /// bytes, row-major, no row padding — exactly what Qt's
    /// `QImage::convertToFormat(QImage.Format_RGBA8888)` produces).
    /// `time` is that source's own playback position in seconds, exposed
    /// to the shader as `iChannelTime[index]`. Safe to call even if this
    /// slot has since been reassigned away from Video/Webcam — such a
    /// frame is silently dropped rather than raising.
    fn update_ichannel_video_frame(
        &mut self,
        pass: usize,
        index: u32,
        width: u32,
        height: u32,
        rgba: &[u8],
        time: f32,
    ) -> PyResult<()> {
        self.inner
            .update_ichannel_video_frame(pass, index, width, height, rgba, time)
            .map_err(to_py_err)
    }

    /// Points a pass's iChannel slot at an audio file, matching
    /// Shadertoy's audio iChannel source option. Decoding and FFT happen
    /// entirely on the Python side (`audio_source.py`); this call only
    /// allocates the fixed 512x2 spectrum/waveform texture so the slot has
    /// something valid (silence) bound immediately. Actual frames arrive
    /// via `update_ichannel_audio_frame`.
    fn set_ichannel_audio(&mut self, pass: usize, index: u32) -> PyResult<()> {
        self.inner.set_ichannel_audio(pass, index).map_err(to_py_err)
    }

    /// Uploads one already-computed spectrum/waveform frame to a pass's
    /// iChannel audio slot. `spectrum`/`waveform` must each be exactly 512
    /// bytes (row 0/row 1 of the audio channel texture, 0-255 already
    /// normalized on the Python side). `time` is that source's own
    /// playback position in seconds, exposed to the shader as
    /// `iChannelTime[index]`. Safe to call even if this slot has since
    /// been reassigned away from Audio (silently dropped, same as
    /// `update_ichannel_video_frame`).
    fn update_ichannel_audio_frame(
        &mut self,
        pass: usize,
        index: u32,
        spectrum: Vec<u8>,
        waveform: Vec<u8>,
        time: f32,
    ) -> PyResult<()> {
        let spectrum: [u8; 512] = spectrum
            .try_into()
            .map_err(|v: Vec<u8>| to_py_err(format!("spectrum: 512 octets attendus, {} reçus", v.len())))?;
        let waveform: [u8; 512] = waveform
            .try_into()
            .map_err(|v: Vec<u8>| to_py_err(format!("waveform: 512 octets attendus, {} reçus", v.len())))?;
        self.inner
            .update_ichannel_audio_frame(pass, index, &spectrum, &waveform, time)
            .map_err(to_py_err)
    }

    fn clear_ichannel(&mut self, pass: usize, index: u32) -> PyResult<()> {
        self.inner.clear_ichannel(pass, index).map_err(to_py_err)
    }

    /// Re-uploads the shared `iKeyboard` texture from the UI's current
    /// keyboard state. `down`/`pressed`/`toggled` are each exactly 256
    /// bytes (one per JS-style legacy `keyCode`), `0` or non-zero. Cheap
    /// to call every frame unconditionally, same as the other per-frame
    /// globals.
    fn update_keyboard(&mut self, down: Vec<u8>, pressed: Vec<u8>, toggled: Vec<u8>) -> PyResult<()> {
        self.inner.update_keyboard(&down, &pressed, &toggled).map_err(to_py_err)
    }

    /// Reallocates the output/buffer textures at a new resolution (e.g.
    /// the viewport widget was resized). Buffer contents are reset.
    fn resize(&mut self, width: u32, height: u32) {
        self.inner.resize(width, height);
    }

    /// Renders one frame (all compiled Buffer passes, in order, then the
    /// Image pass) and returns the Image pass's raw RGBA8 pixel bytes
    /// (row-major, no padding), ready for QImage(data, w, h, Format_RGBA8888).
    fn render<'py>(
        &mut self,
        py: Python<'py>,
        time: f32,
        time_delta: f32,
        mouse: (f32, f32, f32, f32),
        frame: u32,
        date: (f32, f32, f32, f32),
    ) -> PyResult<Bound<'py, PyBytes>> {
        let pixels = self
            .inner
            .render(time, time_delta, mouse, frame, date)
            .map_err(to_py_err)?;
        Ok(PyBytes::new_bound(py, &pixels))
    }
}

/// Scans plain GLSL source for tunable float literals (no annotation
/// syntax): each becomes a slider candidate with a character-offset range
/// so the UI can rewrite it in place when the slider moves.
#[pyfunction]
fn detect_literal_sliders(source: &str) -> Vec<LiteralSlider> {
    literals::detect_literal_sliders(source)
        .into_iter()
        .map(LiteralSlider::from)
        .collect()
}

/// Scans plain GLSL source for every supported slider-candidate kind at
/// once: float, int, bool, and grouped `vec2`/`vec3`/`vec4` literal calls.
/// Returns `(floats, ints, bools, vecs)`.
#[pyfunction]
fn detect_all_sliders(
    source: &str,
) -> (Vec<LiteralSlider>, Vec<IntSlider>, Vec<BoolSlider>, Vec<VecSlider>) {
    let detected = literals::detect_all_sliders(source);
    (
        detected.floats.into_iter().map(LiteralSlider::from).collect(),
        detected.ints.into_iter().map(IntSlider::from).collect(),
        detected.bools.into_iter().map(BoolSlider::from).collect(),
        detected.vecs.into_iter().map(VecSlider::from).collect(),
    )
}

/// Returns how many lines of the generated Shadertoy harness (plus the
/// `Common` source, if any) precede a pass's own code, so compile-error
/// line numbers (which refer to the fully wrapped source) can be
/// translated back to the line the user sees in that pass's editor tab.
#[pyfunction]
fn fragment_header_line_count(common: &str, source: &str) -> usize {
    shader::header_line_count(common, source)
}

/// Minifies GLSL source: strips comments/whitespace and shortens
/// user-defined identifiers, leaving GLSL keywords/builtins and the
/// Shadertoy harness globals (iResolution, iTime, mainImage, ...) intact.
/// Numeric literals (slider values) are never touched.
#[pyfunction]
fn golf_shader(source: &str) -> String {
    golf::golf_shader(source)
}

/// Minifies the `Common` source. Never renames identifiers (unlike
/// `golf_shader`): Common's declared names are referenced from other,
/// separately-golfed passes and must stay textually stable for those
/// calls to keep resolving — use together with `golf_shader_with_common`.
#[pyfunction]
fn golf_common(source: &str) -> String {
    golf::golf_common(source)
}

/// Minifies one pass's source the same way `golf_shader` does, but also
/// protects every identifier appearing in `common_source` (the original,
/// un-golfed `Common` text) from being renamed — so a pass that calls a
/// Common-declared helper keeps calling it by the same name Common itself
/// was golfed with (see `golf_common`).
#[pyfunction]
fn golf_shader_with_common(source: &str, common_source: &str) -> String {
    golf::golf_shader_with_common(source, common_source)
}

/// `golf_shader`/`golf_shader_with_common` with the two "aggressive"
/// transforms independently toggleable: identifier renaming and dead-code
/// elimination (unused top-level functions/structs), and `algebra` toggles
/// algebraic simplification (`x*0.` -> `0.`, `x*1.` dropped, ...). Comments,
/// whitespace, numeric-literal shortening and redundant-semicolon collapsing
/// always happen regardless — this is the "golf aggressiveness level"
/// control. Pass `common_source=""` when there's no Common tab in use.
#[pyfunction]
#[pyo3(signature = (source, common_source="", rename=true, dead_code=true, algebra=true))]
fn golf_shader_ex(source: &str, common_source: &str, rename: bool, dead_code: bool, algebra: bool) -> String {
    golf::golf_shader_ex(source, common_source, rename, dead_code, algebra)
}

#[pymodule]
fn shadertoy_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Engine>()?;
    m.add_class::<LiteralSlider>()?;
    m.add_class::<IntSlider>()?;
    m.add_class::<BoolSlider>()?;
    m.add_class::<VecSlider>()?;
    m.add_function(wrap_pyfunction!(detect_literal_sliders, m)?)?;
    m.add_function(wrap_pyfunction!(detect_all_sliders, m)?)?;
    m.add_function(wrap_pyfunction!(golf_shader, m)?)?;
    m.add_function(wrap_pyfunction!(golf_common, m)?)?;
    m.add_function(wrap_pyfunction!(golf_shader_with_common, m)?)?;
    m.add_function(wrap_pyfunction!(golf_shader_ex, m)?)?;
    m.add_function(wrap_pyfunction!(fragment_header_line_count, m)?)?;
    m.add("PASS_BUFFER_A", renderer::PASS_BUFFER_A)?;
    m.add("PASS_BUFFER_B", renderer::PASS_BUFFER_B)?;
    m.add("PASS_BUFFER_C", renderer::PASS_BUFFER_C)?;
    m.add("PASS_BUFFER_D", renderer::PASS_BUFFER_D)?;
    m.add("PASS_IMAGE", renderer::PASS_IMAGE)?;
    Ok(())
}
