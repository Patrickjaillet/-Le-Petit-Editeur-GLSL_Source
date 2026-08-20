/// A GLSL float literal found in the shader's own source, treated as a
/// slider candidate. There is no annotation syntax involved: the shader
/// stays 100% Shadertoy-compatible (paste-in / paste-out). `start`/`end`
/// are *character* offsets (not bytes) into the source string, matching
/// how both Python (`str` indexing) and Monaco's `model.getPositionAt`
/// address text, so the UI can rewrite the literal in place.
#[derive(Debug, Clone)]
pub struct LiteralSlider {
    pub start: usize,
    pub end: usize,
    pub value: f32,
    pub min: f32,
    pub max: f32,
    pub category: String,
}

/// A bare (no `.`/exponent) integer literal — same idea as `LiteralSlider`
/// but for `int` sliders. Detected separately from floats since GLSL's
/// grammar (and the meaning to a human reading the shader) treats `4` and
/// `4.0` quite differently, and Shadertoy authors do use plain ints as
/// tunable knobs (iteration counts, octaves, ...).
#[derive(Debug, Clone)]
pub struct IntSlider {
    pub start: usize,
    pub end: usize,
    pub value: i32,
    pub min: i32,
    pub max: i32,
    pub category: String,
}

/// A `true`/`false` literal, exposed as a toggle. `start`/`end` span
/// exactly the 4 or 5 characters of the keyword, so flipping it just
/// rewrites that span with the other keyword (same length or not, the
/// caller's offset-shifting logic already handles either case).
#[derive(Debug, Clone)]
pub struct BoolSlider {
    pub start: usize,
    pub end: usize,
    pub value: bool,
    pub category: String,
}

/// A `vec2(a, b)` / `vec3(a, b, c)` / `vec4(a, b, c, d)` constructor call
/// whose every argument is a plain float literal — grouped into a single
/// slider candidate (a color picker for `vec3`/`vec4`, an X/Y pair for
/// `vec2`) instead of `size` separate float sliders. `start`/`end` span
/// the *entire* call, from the `v` of `vec2`/`vec3`/`vec4` to the closing
/// `)`: editing this slider replaces the whole call text at once
/// (reformatting all components together) rather than tracking `size`
/// independent sub-ranges.
///
/// Deliberately narrow: only a literal-only argument list of exactly the
/// right arity is recognized. `vec3(0.5)` (splat), `vec3(a, b, c)`
/// (expressions), etc. fall through and their arguments — if they
/// themselves are plain literals — still surface as ordinary standalone
/// float sliders, same as before this grouping existed.
#[derive(Debug, Clone)]
pub struct VecSlider {
    pub start: usize,
    pub end: usize,
    pub size: u8,
    pub values: Vec<f32>,
    pub category: String,
}

#[derive(Debug, Clone, Default)]
pub struct DetectedSliders {
    pub floats: Vec<LiteralSlider>,
    pub ints: Vec<IntSlider>,
    pub bools: Vec<BoolSlider>,
    pub vecs: Vec<VecSlider>,
}

fn is_ident_start(c: char) -> bool {
    c.is_ascii_alphabetic() || c == '_'
}

fn is_ident_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_'
}

/// True if the `-` at `dash_i` reads as a unary (sign) minus rather than
/// binary subtraction — i.e. it does *not* immediately follow something
/// that produces a value (an identifier/keyword, another number, `)`, or
/// `]`). Looks back past whitespace to the previous significant
/// character; start-of-source counts as unary.
///
/// This matters because `format_glsl_float`/int formatting on the Python
/// side writes the sign as part of the literal's own text (`-3.0`), but
/// without this check the scanner below would only ever capture the
/// digits, leaving a slider-written `-` sitting just outside the tracked
/// span — invisible to `start`/`end` bookkeeping. Every later resync then
/// re-detects a *shorter* literal starting right after that stray `-`, so
/// the next negative write glues another sign in front of it instead of
/// replacing it, and repeated drags/resyncs pile up runs of `-`.
fn is_unary_minus_context(chars: &[char], dash_i: usize) -> bool {
    let mut k = dash_i;
    while k > 0 {
        k -= 1;
        let c = chars[k];
        if c == ' ' || c == '\t' || c == '\n' || c == '\r' {
            continue;
        }
        return !(is_ident_char(c) || c == ')' || c == ']');
    }
    true
}

/// Attempts to scan a float literal (requires a `.` or exponent, so plain
/// integers like loop bounds/array sizes are never matched) starting at
/// `i`. Returns the end index on success.
fn try_scan_float(chars: &[char], i: usize) -> Option<usize> {
    let n = chars.len();
    let mut j = i;
    let mut saw_digit_before = false;
    while j < n && chars[j].is_ascii_digit() {
        j += 1;
        saw_digit_before = true;
    }
    let mut has_dot = false;
    let mut saw_digit_after = false;
    if j < n && chars[j] == '.' && (saw_digit_before || (j + 1 < n && chars[j + 1].is_ascii_digit())) {
        has_dot = true;
        j += 1;
        while j < n && chars[j].is_ascii_digit() {
            j += 1;
            saw_digit_after = true;
        }
    }
    if !saw_digit_before && !saw_digit_after {
        return None;
    }
    let mut has_exp = false;
    if j < n && (chars[j] == 'e' || chars[j] == 'E') {
        let mut k = j + 1;
        if k < n && (chars[k] == '+' || chars[k] == '-') {
            k += 1;
        }
        if k < n && chars[k].is_ascii_digit() {
            while k < n && chars[k].is_ascii_digit() {
                k += 1;
            }
            j = k;
            has_exp = true;
        }
    }
    if !has_dot && !has_exp {
        return None; // plain integer: not a tunable float literal
    }
    Some(j)
}

/// Scans a plain (no `.`/exponent) run of digits starting at `i`. Only
/// call this after `try_scan_float` has already failed at `i`, so it's
/// known not to be the start of a float.
fn scan_plain_int(chars: &[char], i: usize) -> usize {
    let n = chars.len();
    let mut j = i;
    while j < n && chars[j].is_ascii_digit() {
        j += 1;
    }
    j
}

/// Looks ahead from just past a `vec2`/`vec3` identifier for `(` followed
/// by exactly `arity` comma-separated plain float literals and `)`. On
/// success, returns the index just past the closing `)` and the parsed
/// values (in source order).
fn try_scan_vec_call(chars: &[char], ident_end: usize, arity: usize) -> Option<(usize, Vec<f32>)> {
    let n = chars.len();
    let mut j = ident_end;
    while j < n && (chars[j] == ' ' || chars[j] == '\t') {
        j += 1;
    }
    if j >= n || chars[j] != '(' {
        return None;
    }
    j += 1;

    let mut values = Vec::with_capacity(arity);
    loop {
        while j < n && chars[j].is_whitespace() {
            j += 1;
        }
        // A leading `-` right here is always a unary sign, never binary
        // subtraction: an argument list only ever puts `(` or `,`
        // immediately before an argument (whitespace already skipped
        // above), and neither is a "value" in `is_unary_minus_context`'s
        // sense, so the check that function performs for the top-level
        // scanner is unconditionally true at this position — no need to
        // call it. Fold the sign into the parsed literal exactly like
        // `detect_all_sliders`'s main loop does (see M1 in AUDIT.md):
        // without this, `try_scan_float` alone can't start on a `-`, so
        // `vec3(-1.0, 0.5, 0.2)` / `vec2(-0.3, 0.4)` failed to parse here
        // at all and the whole call fell through to individual float
        // sliders instead of being grouped.
        let literal_start = j;
        let digits_start = if j < n
            && chars[j] == '-'
            && j + 1 < n
            && (chars[j + 1].is_ascii_digit()
                || (chars[j + 1] == '.' && j + 2 < n && chars[j + 2].is_ascii_digit()))
        {
            j + 1
        } else {
            j
        };
        let end = try_scan_float(chars, digits_start)?;
        let text: String = chars[literal_start..end].iter().collect();
        values.push(text.parse::<f32>().ok()?);
        j = end;
        while j < n && chars[j].is_whitespace() {
            j += 1;
        }
        if j >= n {
            return None;
        }
        if chars[j] == ',' {
            j += 1;
            continue;
        }
        if chars[j] == ')' {
            j += 1;
            break;
        }
        return None; // anything else (an expression, a swizzle, ...): not a pure-literal call
    }

    if values.len() != arity {
        return None;
    }
    Some((j, values))
}

fn default_float_range(value: f32) -> (f32, f32) {
    if value == 0.0 {
        (-1.0, 1.0)
    } else if value > 0.0 {
        (0.0, value * 2.0)
    } else {
        (value * 2.0, 0.0)
    }
}

fn default_int_range(value: i32) -> (i32, i32) {
    if value == 0 {
        (-10, 10)
    } else if value > 0 {
        (0, value * 2)
    } else {
        (value * 2, 0)
    }
}

/// Recognizes a "section marker" comment line like `// -- Couleur --`
/// (at least 2 dashes framing non-empty text on both ends) used to
/// subdivide one function's sliders into named sub-categories, finer than
/// the whole-function grouping `category_for` otherwise falls back to.
/// Deliberately strict (dashes required on both sides) so an ordinary
/// comment is never mistaken for one.
fn parse_section_marker(comment_text: &str) -> Option<String> {
    let t = comment_text.trim();
    let leading = t.chars().take_while(|&c| c == '-').count();
    if leading < 2 {
        return None;
    }
    let trailing = t.chars().rev().take_while(|&c| c == '-').count();
    if trailing < 2 || leading + trailing >= t.len() {
        return None; // `>=` also rejects an all-dashes line like "----"
    }
    let inner = t[leading..t.len() - trailing].trim();
    if inner.is_empty() {
        None
    } else {
        Some(inner.to_string())
    }
}

/// Resolves the display category for whatever position `func_stack`/
/// `global_section` currently describe: the innermost function's name,
/// suffixed with its active section marker if one is in effect (reset to
/// none each time a new function is entered), or "Global"/"Global — X"
/// outside any function.
fn category_for(func_stack: &[(String, i32, Option<String>)], global_section: &Option<String>) -> String {
    if let Some((name, _, section)) = func_stack.last() {
        match section {
            Some(s) => format!("{name} — {s}"),
            None => name.clone(),
        }
    } else {
        match global_section {
            Some(s) => format!("Global — {s}"),
            None => "Global".to_string(),
        }
    }
}

/// Scans plain (unannotated) GLSL source for tunable literals of every
/// supported kind (float, int, bool, and grouped `vec2`/`vec3` calls) in a
/// single pass.
///
/// Excludes:
/// - text inside `//` and `/* */` comments
/// - preprocessor directive lines (`#version`, `#define`, ...)
/// - literals inside a `for (...)` loop header (almost always
///   counters/bounds, not tunable parameters)
///
/// Each literal is categorized by its innermost enclosing top-level
/// function name (GLSL forbids nested function definitions, so any `{`
/// opened at brace-depth 0 is a function body), falling back to "Global"
/// for literals outside any function. A `// -- Section --`-style comment
/// line (2+ dashes framing non-empty text on both sides, see
/// `parse_section_marker`) further splits a function's own literals into
/// named sub-categories ("FonctionName — Section") from that point until
/// either the next such marker or the end of the function, whichever
/// comes first — each function starts with no active section, so this
/// never leaks across function boundaries.
pub fn detect_all_sliders(src: &str) -> DetectedSliders {
    let chars: Vec<char> = src.chars().collect();
    let n = chars.len();
    let mut result = DetectedSliders::default();

    let mut i = 0usize;
    let mut at_line_start = true;
    let mut depth: i32 = 0;
    let mut func_stack: Vec<(String, i32, Option<String>)> = Vec::new();
    let mut global_section: Option<String> = None;
    let mut pending_ident: Option<String> = None;
    let mut paren_stack: Vec<bool> = Vec::new(); // true = inside a `for (...)` header

    while i < n {
        let c = chars[i];

        if at_line_start {
            let mut j = i;
            while j < n && (chars[j] == ' ' || chars[j] == '\t') {
                j += 1;
            }
            if j < n && chars[j] == '#' {
                while i < n && chars[i] != '\n' {
                    i += 1;
                }
                continue;
            }
        }
        at_line_start = false;

        if c == '/' && i + 1 < n && chars[i + 1] == '/' {
            let text_start = i + 2;
            let mut j = text_start;
            while j < n && chars[j] != '\n' {
                j += 1;
            }
            let line_text: String = chars[text_start..j].iter().collect();
            if let Some(section) = parse_section_marker(&line_text) {
                if let Some(frame) = func_stack.last_mut() {
                    frame.2 = Some(section);
                } else {
                    global_section = Some(section);
                }
            }
            i = j;
            continue;
        }
        if c == '/' && i + 1 < n && chars[i + 1] == '*' {
            i += 2;
            while i + 1 < n && !(chars[i] == '*' && chars[i + 1] == '/') {
                if chars[i] == '\n' {
                    at_line_start = true;
                }
                i += 1;
            }
            i = (i + 2).min(n);
            continue;
        }
        if c == '\n' {
            at_line_start = true;
            i += 1;
            continue;
        }

        if is_ident_start(c) {
            let start = i;
            while i < n && is_ident_char(chars[i]) {
                i += 1;
            }
            let name: String = chars[start..i].iter().collect();
            let masked = paren_stack.iter().any(|&b| b);

            if !masked && (name == "vec2" || name == "vec3" || name == "vec4") {
                let arity = match name.as_str() {
                    "vec2" => 2,
                    "vec3" => 3,
                    _ => 4,
                };
                if let Some((call_end, values)) = try_scan_vec_call(&chars, i, arity) {
                    let category = category_for(&func_stack, &global_section);
                    result.vecs.push(VecSlider {
                        start,
                        end: call_end,
                        size: arity as u8,
                        values,
                        category,
                    });
                    i = call_end;
                    continue;
                }
            }

            if !masked && (name == "true" || name == "false") {
                let category = category_for(&func_stack, &global_section);
                result.bools.push(BoolSlider {
                    start,
                    end: i,
                    value: name == "true",
                    category,
                });
            }

            // `pending_ident` only needs to stay unset while we're inside an
            // *open* paren list (`paren_stack.is_empty()`) — that's what
            // keeps a function's own name correct while scanning its
            // parameter list (e.g. `mainImage(out vec4 fragColor, ...)`:
            // `fragColor`/`fragCoord` never overwrite `pending_ident` because
            // `paren_stack` isn't empty while inside `(...)`). There is no
            // reason to *also* require `depth == 0`: the only other
            // consumer, the `{` handler just below, already gates its own
            // read of `pending_ident` on `depth == 0` by itself, so nothing
            // downstream needs identifiers at `depth >= 1` to be ignored
            // here. Requiring `depth == 0` used to silently break `for(...)`
            // masking for the by-far-most-common case (a `for` loop written
            // inside `mainImage` or any helper function, i.e. `depth >= 1`):
            // `pending_ident` would never become `Some("for")` again once
            // inside a function body, so the `(` handler's `is_for` check
            // below always saw `false` and the loop header's own bounds
            // leaked through as ordinary int/float sliders instead of being
            // masked (previously a known bug, see the regression test
            // `for_loop_header_inside_a_function_is_not_masked_known_bug`).
            if paren_stack.is_empty() {
                pending_ident = Some(name);
            }
            continue;
        }

        if c == '(' {
            let is_for = pending_ident.as_deref() == Some("for");
            paren_stack.push(is_for);
            i += 1;
            continue;
        }
        if c == ')' {
            paren_stack.pop();
            i += 1;
            continue;
        }
        if c == '{' {
            if depth == 0 {
                let name = pending_ident.clone().unwrap_or_else(|| "Global".to_string());
                func_stack.push((name, depth, None));
            }
            depth += 1;
            i += 1;
            continue;
        }
        if c == '}' {
            depth -= 1;
            if let Some((_, at_depth, _)) = func_stack.last() {
                if *at_depth == depth {
                    func_stack.pop();
                }
            }
            i += 1;
            continue;
        }

        // A numeric literal, optionally preceded by a *unary* minus that
        // belongs to it (`-5.0`, not `a - 5.0`). Folding a genuine sign
        // into `start` keeps the tracked span in sync with what the UI
        // actually writes back (which always includes the sign) — see
        // `is_unary_minus_context` for why this is required.
        let digits_start = if c == '-'
            && i + 1 < n
            && (chars[i + 1].is_ascii_digit()
                || (chars[i + 1] == '.' && i + 2 < n && chars[i + 2].is_ascii_digit()))
            && is_unary_minus_context(&chars, i)
        {
            Some(i + 1)
        } else if c.is_ascii_digit() || (c == '.' && i + 1 < n && chars[i + 1].is_ascii_digit()) {
            Some(i)
        } else {
            None
        };

        if let Some(ds) = digits_start {
            // Hex integer literal (`0x1F`, `0X2a`) -- GLSL allows these, but
            // this detector has no notion of hex-formatted output. Without
            // this check, `try_scan_float` fails (no `.`/exponent) and the
            // plain-int fallback below would scan only the leading `0`,
            // leaving `x1F` dangling as ordinary source text right after a
            // slider-controlled span -- so dragging that "slider" would
            // rewrite `0x1F` into e.g. `5x1F`, corrupting the shader instead
            // of just being a merely unhelpful control. Skipped wholesale
            // instead, like a `for(...)` header: a documented exception, not
            // a silently truncated one.
            if chars[ds] == '0'
                && ds + 2 < n
                && (chars[ds + 1] == 'x' || chars[ds + 1] == 'X')
                && chars[ds + 2].is_ascii_hexdigit()
            {
                let mut k = ds + 2;
                while k < n && chars[k].is_ascii_hexdigit() {
                    k += 1;
                }
                i = k;
                continue;
            }
            let masked = paren_stack.iter().any(|&b| b);
            if let Some(end) = try_scan_float(&chars, ds) {
                if !masked {
                    let text: String = chars[i..end].iter().collect();
                    if let Ok(value) = text.parse::<f32>() {
                        let (min, max) = default_float_range(value);
                        let category = category_for(&func_stack, &global_section);
                        result.floats.push(LiteralSlider {
                            start: i,
                            end,
                            value,
                            min,
                            max,
                            category,
                        });
                    }
                }
                i = end;
                continue;
            }
            // Not a float (no `.`/exponent): a plain integer, unless it's a
            // lone `.` with no trailing digit either (handled by falling
            // through to `i += 1` below, same as before this function grew
            // int support).
            if chars[ds].is_ascii_digit() {
                let end = scan_plain_int(&chars, ds);
                if !masked {
                    let text: String = chars[i..end].iter().collect();
                    if let Ok(value) = text.parse::<i32>() {
                        let (min, max) = default_int_range(value);
                        let category = category_for(&func_stack, &global_section);
                        result.ints.push(IntSlider {
                            start: i,
                            end,
                            value,
                            min,
                            max,
                            category,
                        });
                    }
                }
                i = end;
                continue;
            }
        }

        i += 1;
    }

    result
}

/// Float-only view of `detect_all_sliders`, kept for callers that only
/// care about float sliders.
pub fn detect_literal_sliders(src: &str) -> Vec<LiteralSlider> {
    detect_all_sliders(src).floats
}

// ---------------------------------------------------------------------
// Tests — see M2 in AUDIT.md: `detect_all_sliders` (the real native
// detector every slider ultimately comes from) previously had zero
// automated coverage; `test_sliders.py` only ever exercised `SlidersPanel`
// against hand-built `Fake*` objects, never real GLSL text run through
// this parser. These tests call the actual entry points
// (`detect_all_sliders`/`detect_literal_sliders`) end-to-end on GLSL
// snippets, the same way `engine_bridge.detect_all_sliders` does from
// Python, so a regression here fails `cargo test` immediately instead of
// only surfacing later as a UI bug report (see M1, caught by exactly this
// kind of test).
// ---------------------------------------------------------------------
#[cfg(test)]
mod tests {
    use super::*;

    // ---- masking: comments, directives, `for(...)` headers -----------

    fn cat(kind_counts: &DetectedSliders) -> (usize, usize, usize, usize) {
        (
            kind_counts.floats.len(),
            kind_counts.ints.len(),
            kind_counts.bools.len(),
            kind_counts.vecs.len(),
        )
    }

    #[test]
    fn ignores_literals_inside_line_comments() {
        let r = detect_all_sliders("// EPS = 0.0001, count = 4, ok = true\nfloat a = 1.0;\n");
        assert_eq!(cat(&r), (1, 0, 0, 0), "only the real `1.0` outside the comment must surface");
        assert_eq!(r.floats[0].value, 1.0);
    }

    #[test]
    fn ignores_literals_inside_block_comments() {
        let r = detect_all_sliders("/* old code: float x = 2.0; int n = 5; */\nfloat a = 1.0;\n");
        assert_eq!(cat(&r), (1, 0, 0, 0));
        assert_eq!(r.floats[0].value, 1.0);
    }

    #[test]
    fn ignores_literals_inside_preprocessor_directives() {
        let r = detect_all_sliders("#define EPS 0.0001\nfloat a = 1.0;\n");
        assert_eq!(cat(&r), (1, 0, 0, 0), "#define's own literal must not surface as a slider");
        assert_eq!(r.floats[0].value, 1.0);
    }

    #[test]
    fn ignores_literals_inside_top_level_for_loop_header() {
        // `for` written directly at file scope (depth 0, not inside any
        // function's `{}`) -- the case `pending_ident` is actually able to
        // capture (see the depth==0 guard around its assignment in
        // `detect_all_sliders`).
        let r = detect_all_sliders("for(int i = 0; i < 8; i++) { float a = 1.0; }\n");
        assert_eq!(cat(&r), (1, 0, 0, 0), "for(...) header literals must be masked: {:?}", r.floats);
        assert_eq!(r.floats[0].value, 1.0);
    }

    #[test]
    fn for_loop_header_inside_a_function_is_masked() {
        // Formerly a known bug (tracked as "M5" in AUDIT.md, which itself
        // never got a dedicated write-up beyond a dangling cross-reference
        // from M2) -- discovered while wiring up a real cargo/rustc
        // toolchain for the first time and actually running this test
        // suite as a whole crate instead of relexing `literals.rs` in
        // isolation. `pending_ident` used to only update while
        // `depth == 0 && paren_stack.is_empty()`, i.e. only for
        // identifiers seen at *file* scope -- so a `for` keyword written
        // inside any function's `{}` (`depth >= 1`, which is essentially
        // every real shader's `for` loop: always inside `mainImage` or a
        // helper function) never made `pending_ident` become
        // `Some("for")` again, silently leaking the loop header's own
        // bounds through as ordinary int/float sliders instead of masking
        // them like the top-level case above. Fixed by dropping the
        // `depth == 0` half of that condition -- `paren_stack.is_empty()`
        // alone is what actually matters (it's what keeps a function's
        // own name from being clobbered by its parameter names while
        // scanning `(...)`), and nothing downstream needs identifiers at
        // `depth >= 1` to be ignored on top of that.
        let src = "void mainImage(out vec4 fragColor, in vec2 fragCoord) {\n\
                   for (int i = 0; i < 8; i++) {\n\
                       float a = 1.0;\n\
                   }\n\
                   }\n";
        let r = detect_all_sliders(src);
        // Same shape as the top-level case: only the loop body's real
        // `1.0` surfaces as a slider, the loop header's `0`/`8` bounds
        // stay masked.
        assert_eq!(cat(&r), (1, 0, 0, 0), "for(...) header literals must be masked even inside a function body: {:?}", r.floats);
        assert_eq!(r.floats[0].value, 1.0);
    }

    // ---- unary minus vs. binary subtraction (top-level scanner) ------

    #[test]
    fn unary_minus_is_folded_into_the_literal() {
        let r = detect_all_sliders("float a = -1.0;\n");
        assert_eq!(cat(&r), (1, 0, 0, 0));
        assert_eq!(r.floats[0].value, -1.0);
        // The `-` must be part of the tracked span, not sitting just
        // before `start` (see `is_unary_minus_context`'s docstring for why
        // that matters for offset bookkeeping).
        assert_eq!(&r.floats[0].start, &10usize);
    }

    #[test]
    fn binary_subtraction_is_not_folded() {
        let r = detect_all_sliders("float a = b - 1.0;\n");
        assert_eq!(cat(&r), (1, 0, 0, 0), "`b - 1.0` has exactly one literal: `1.0`, unsigned");
        assert_eq!(r.floats[0].value, 1.0);
    }

    #[test]
    fn minus_after_closing_paren_or_bracket_is_subtraction() {
        // No space between `)`/`]` and `-1.0`/`-2.0` on purpose: this is
        // exactly the case `is_unary_minus_context` exists for -- a `-`
        // immediately touching a digit *would* otherwise look identical to
        // a genuine unary sign, so this only proves anything when the
        // digit directly follows the minus.
        let r = detect_all_sliders("float a = foo()-1.0;\nfloat b = arr[0]-2.0;\n");
        assert_eq!(cat(&r), (2, 1, 0, 0));
        // `arr[0]`'s `0` is a bare int (unsigned); `-1.0`/`-2.0` are both
        // unsigned floats since `)`/`]` precede the minus, making it
        // subtraction rather than a sign.
        assert_eq!(r.floats[0].value, 1.0);
        assert_eq!(r.floats[1].value, 2.0);
        assert_eq!(r.ints[0].value, 0);
    }

    // ---- vec2/vec3/vec4 grouping: positive / negative / mixed --------

    #[test]
    fn groups_vec3_with_all_positive_components() {
        let r = detect_all_sliders("vec3 col = vec3(0.1, 0.2, 0.3);\n");
        assert_eq!(cat(&r), (0, 0, 0, 1));
        assert_eq!(r.vecs[0].values, vec![0.1f32, 0.2, 0.3]);
    }

    #[test]
    fn groups_vec3_with_negative_components() {
        // The M1 regression: previously fell through to 3 separate floats.
        let r = detect_all_sliders("vec3 dir = vec3(-1.0, 0.5, 0.2);\n");
        assert_eq!(cat(&r), (0, 0, 0, 1));
        assert_eq!(r.vecs[0].values, vec![-1.0f32, 0.5, 0.2]);
    }

    #[test]
    fn groups_vec2_with_negative_components() {
        let r = detect_all_sliders("vec2 off = vec2(-0.3, 0.4);\n");
        assert_eq!(cat(&r), (0, 0, 0, 1));
        assert_eq!(r.vecs[0].values, vec![-0.3f32, 0.4]);
        assert_eq!(r.vecs[0].size, 2);
    }

    #[test]
    fn groups_vec3_with_all_negative_components() {
        let r = detect_all_sliders("vec3 v = vec3(-0.1, -0.2, -0.3);\n");
        assert_eq!(cat(&r), (0, 0, 0, 1));
        assert_eq!(r.vecs[0].values, vec![-0.1f32, -0.2, -0.3]);
    }

    #[test]
    fn groups_vec4_with_all_positive_components() {
        // m1: vec4 was previously never recognized by name at all, so an
        // RGBA constant like this always fell through to 4 separate float
        // sliders instead of one grouped color+alpha control.
        let r = detect_all_sliders("vec4 col = vec4(1.0, 0.5, 0.2, 1.0);\n");
        assert_eq!(cat(&r), (0, 0, 0, 1));
        assert_eq!(r.vecs[0].values, vec![1.0f32, 0.5, 0.2, 1.0]);
        assert_eq!(r.vecs[0].size, 4);
    }

    #[test]
    fn groups_vec4_with_negative_components() {
        let r = detect_all_sliders("vec4 v = vec4(-1.0, 0.5, -0.2, 1.0);\n");
        assert_eq!(cat(&r), (0, 0, 0, 1));
        assert_eq!(r.vecs[0].values, vec![-1.0f32, 0.5, -0.2, 1.0]);
        assert_eq!(r.vecs[0].size, 4);
    }

    #[test]
    fn vec4_splat_is_not_grouped() {
        let r = detect_all_sliders("vec4 v = vec4(0.5);\n");
        assert_eq!(cat(&r), (1, 0, 0, 0), "single-argument splat must fall through to one float slider");
    }

    #[test]
    fn vec4_with_three_args_is_not_grouped_wrong_arity() {
        // Not valid GLSL as written, but the scanner must still reject it
        // by arity rather than mis-grouping — same guard as vec2/vec3.
        let r = detect_all_sliders("vec4 v = vec4(0.1, 0.2, 0.3);\n");
        assert_eq!(cat(&r), (3, 0, 0, 0));
    }

    // ---- splat / expressions: deliberately NOT grouped ----------------

    #[test]
    fn splat_is_not_grouped_but_its_literal_still_surfaces() {
        let r = detect_all_sliders("vec3 grey = vec3(0.5);\n");
        assert_eq!(cat(&r), (1, 0, 0, 0), "single-argument splat must fall through to one float slider");
        assert_eq!(r.floats[0].value, 0.5);
    }

    #[test]
    fn expression_arguments_are_not_grouped() {
        let r = detect_all_sliders("vec3 v = vec3(a, b, c);\n");
        assert_eq!(cat(&r), (0, 0, 0, 0), "identifiers aren't literals: nothing to group or surface");
    }

    #[test]
    fn mixed_literal_and_expression_arguments_are_not_grouped() {
        let r = detect_all_sliders("vec3 v = vec3(a - 1.0, 0.5, 0.2);\n");
        // One non-literal argument disqualifies the whole call from
        // grouping; the call's own literal-looking arguments then fall
        // through to ordinary top-level scanning like any other
        // parenthesized (non-`for`) expression -- same as the splat case
        // above, just with 3 individual floats instead of 1.
        assert_eq!(cat(&r), (3, 0, 0, 0));
        assert_eq!(r.floats.iter().map(|f| f.value).collect::<Vec<_>>(), vec![1.0f32, 0.5, 0.2]);
    }

    // ---- bool / int literals -------------------------------------------

    #[test]
    fn detects_bool_literals() {
        let r = detect_all_sliders("bool on = true;\nbool off = false;\n");
        assert_eq!(cat(&r), (0, 0, 2, 0));
        assert!(r.bools[0].value);
        assert!(!r.bools[1].value);
    }

    #[test]
    fn plain_integer_is_int_slider_not_float() {
        let r = detect_all_sliders("int n = 4;\n");
        assert_eq!(cat(&r), (0, 1, 0, 0));
        assert_eq!(r.ints[0].value, 4);
    }

    // ---- section markers (valid / invalid) -----------------------------

    #[test]
    fn valid_section_marker_is_recognized() {
        assert_eq!(parse_section_marker("-- Couleur --"), Some("Couleur".to_string()));
        assert_eq!(parse_section_marker("---Fog---"), Some("Fog".to_string()));
    }

    #[test]
    fn invalid_section_markers_are_rejected() {
        assert_eq!(parse_section_marker("just a comment"), None);
        assert_eq!(parse_section_marker("- one dash -"), None, "needs >= 2 dashes on each side");
        assert_eq!(parse_section_marker("----"), None, "all-dashes line has no inner text");
        assert_eq!(parse_section_marker("--"), None);
    }

    #[test]
    fn section_marker_changes_category_until_next_function() {
        let src = "void mainImage(out vec4 fragColor, in vec2 fragCoord) {\n\
                   // -- Couleur --\n\
                   float a = 1.0;\n\
                   }\n\
                   void other() {\n\
                   float b = 2.0;\n\
                   }\n";
        let r = detect_all_sliders(src);
        assert_eq!(cat(&r), (2, 0, 0, 0));
        assert_eq!(r.floats[0].category, "mainImage — Couleur");
        // A fresh function resets the active section back to none.
        assert_eq!(r.floats[1].category, "other");
    }

    // ---- detect_literal_sliders: float-only convenience view ----------

    #[test]
    fn detect_literal_sliders_returns_floats_only() {
        let src = "float a = 1.0; int n = 4; bool b = true; vec2 v = vec2(1, 2);\n";
        let floats = detect_literal_sliders(src);
        assert_eq!(floats.len(), 1);
        assert_eq!(floats[0].value, 1.0);
    }
}
