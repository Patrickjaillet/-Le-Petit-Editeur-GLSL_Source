//! Canonicalizes GLSL matrix constructors that supply *more* scalar
//! components than the matrix needs -- e.g. `mat2(z, -z.y, z)` (5
//! components: `z`=2, `-z.y`=1, `z`=2, for a `mat2` that only needs 4).
//!
//! Per the GLSL spec, this is legal: "if too many arguments are provided,
//! [...] the extra arguments are not used". Real-world code relies on it
//! as a compact way to write a rotation-style matrix from an existing
//! vector (`mat2(z, -z.y, z)` == `mat2(z.x, z.y, -z.y, z.x)`, dropping the
//! trailing `z.y`) -- shadertoy.com's GL driver accepts it, but `naga`'s
//! GLSL frontend (this engine's only route to the GPU, see `shader.rs`)
//! does not: it produces invalid IR instead of a normal parse error,
//! surfacing as a `wgpu` validation panic-adjacent error deep inside
//! `Device::create_shader_module` ("Composing expects N components but M
//! were given") that means nothing to someone who never touched naga.
//!
//! Rather than wait on upstream, this rewrites the *source text* ahead of
//! naga into the exact, unambiguous form it already parses fine --
//! spelling out precisely which leading components of the trailing
//! argument survive, then dropping the rest. This is a **conservative**
//! rewrite: it only ever fires when every argument's component count can
//! be determined with certainty (see `arg_components`); anything it can't
//! prove is left completely untouched, so the worst case is the same
//! naga error as before, never a silently wrong transform.
//!
//! Only `mat2`/`mat3`/`mat4` are handled -- the constructors this pattern
//! actually shows up on in practice (rotation/shear matrices built from a
//! vector plus a couple of scalars).

/// GLSL scalar/vector types this module can resolve a component count for.
/// Deliberately excludes matrices, samplers, structs, arrays, ... --
/// anything not in this list makes a bare-identifier argument's component
/// count unresolvable, which safely aborts the rewrite for that call.
#[derive(Clone, Copy, PartialEq, Eq)]
enum GlslType {
    Scalar,
    Vec2,
    Vec3,
    Vec4,
}

impl GlslType {
    fn components(self) -> usize {
        match self {
            GlslType::Scalar => 1,
            GlslType::Vec2 => 2,
            GlslType::Vec3 => 3,
            GlslType::Vec4 => 4,
        }
    }

    fn from_keyword(word: &str) -> Option<GlslType> {
        Some(match word {
            "float" | "int" | "uint" | "bool" => GlslType::Scalar,
            "vec2" | "ivec2" | "uvec2" | "bvec2" => GlslType::Vec2,
            "vec3" | "ivec3" | "uvec3" | "bvec3" => GlslType::Vec3,
            "vec4" | "ivec4" | "uvec4" | "bvec4" => GlslType::Vec4,
            _ => return None,
        })
    }
}

type Scope = Vec<(String, GlslType)>;

fn lookup(scopes: &[Scope], name: &str) -> Option<GlslType> {
    scopes.iter().rev().find_map(|scope| {
        scope.iter().rev().find(|(n, _)| n == name).map(|(_, t)| *t)
    })
}

/// Component count of one already-trimmed constructor argument, or `None`
/// if it can't be determined with confidence -- the signal that aborts
/// the rewrite for the whole call (see module docs).
fn arg_components(scopes: &[Scope], text: &str) -> Option<usize> {
    let text = text.trim();
    if text.is_empty() {
        return None;
    }
    // Unary +/- doesn't change the component count of its operand.
    if let Some(rest) = text.strip_prefix('-').or_else(|| text.strip_prefix('+')) {
        return arg_components(scopes, rest);
    }
    // A trailing swizzle always narrows to exactly its own length,
    // regardless of what precedes it (`(a+b).xy`, `foo().x`, `z.y`, ...)
    // -- the one case this module never needs the base expression's own
    // type for.
    if let Some(dot_pos) = text.rfind('.') {
        let suffix = &text[dot_pos + 1..];
        if !suffix.is_empty()
            && suffix.len() <= 4
            && suffix.chars().all(|c| "xyzwrgbastpq".contains(c))
        {
            return Some(suffix.len());
        }
    }
    // A numeric literal is always 1 component -- GLSL identifiers can
    // never start with a digit, so this check can't misfire.
    if text.starts_with(|c: char| c.is_ascii_digit()) {
        return Some(1);
    }
    // A bare identifier: resolvable only via a preceding declaration/
    // parameter of a known type in an still-active scope.
    let is_bare_ident = text.starts_with(|c: char| crate::golf::is_ident_start(c))
        && text.chars().all(crate::golf::is_ident_char);
    if is_bare_ident {
        return lookup(scopes, text).map(GlslType::components);
    }
    None
}

/// Splits a raw argument-list string (the text between a constructor's
/// `(` and `)`) on top-level commas -- i.e. not inside a nested
/// `(...)`/`[...]`.
fn split_top_level_args(text: &str) -> Vec<&str> {
    let mut args = Vec::new();
    let mut depth = 0i32;
    let mut start = 0usize;
    let bytes = text.as_bytes();
    for (i, &b) in bytes.iter().enumerate() {
        match b {
            b'(' | b'[' => depth += 1,
            b')' | b']' => depth -= 1,
            b',' if depth == 0 => {
                args.push(text[start..i].trim());
                start = i + 1;
            }
            _ => {}
        }
    }
    let last = text[start..].trim();
    if !last.is_empty() || !args.is_empty() {
        args.push(last);
    }
    args
}

/// Rewrites one over-supplied constructor's argument list down to exactly
/// `needed` components, keeping every fully-used argument's original text
/// verbatim and truncating only the one argument that straddles the
/// boundary (via a synthesized, parenthesized swizzle -- always
/// syntactically safe regardless of that argument's own precedence).
/// Returns `None` if any argument's component count is unresolvable, or
/// if the total doesn't actually exceed `needed` (nothing to do).
fn rewrite_args(scopes: &[Scope], raw_args: &str, needed: usize) -> Option<String> {
    let args = split_top_level_args(raw_args);
    let mut counts = Vec::with_capacity(args.len());
    let mut total = 0usize;
    for a in &args {
        let c = arg_components(scopes, a)?;
        counts.push(c);
        total += c;
    }
    if total <= needed {
        return None;
    }
    let mut kept: Vec<String> = Vec::new();
    let mut running = 0usize;
    for (arg, count) in args.iter().zip(counts.iter()) {
        if running >= needed {
            break;
        }
        let remaining_needed = needed - running;
        if remaining_needed >= *count {
            kept.push(arg.to_string());
            running += count;
        } else {
            const SWIZZLE: &str = "xyzw";
            let suffix = &SWIZZLE[..remaining_needed];
            kept.push(format!("({arg}).{suffix}"));
            running += remaining_needed;
        }
    }
    Some(kept.join(", "))
}

/// If the text immediately before `open_paren` (a `(`'s char index) looks
/// like `RETTYPE NAME` -- i.e. this paren opens a function's parameter
/// list, not an `if`/`for`/`while` condition -- parses `QUALIFIER* TYPE
/// NAME` entries out of `params_text` and returns them. `None` for
/// anything else (control-flow keywords have only one preceding
/// identifier, never two).
fn parse_params_if_function_signature(
    chars: &[char],
    open_paren: usize,
    params_text: &str,
) -> Option<Vec<(String, GlslType)>> {
    let mut i = open_paren;
    let skip_ws_back = |chars: &[char], mut j: usize| {
        while j > 0 && chars[j - 1].is_whitespace() {
            j -= 1;
        }
        j
    };
    i = skip_ws_back(chars, i);
    let name_end = i;
    while i > 0 && crate::golf::is_ident_char(chars[i - 1]) {
        i -= 1;
    }
    if i == name_end {
        return None; // no identifier directly before '('
    }
    let name_start = i;
    i = skip_ws_back(chars, i);
    let ret_end = i;
    while i > 0 && crate::golf::is_ident_char(chars[i - 1]) {
        i -= 1;
    }
    if i == ret_end || name_start == ret_end {
        return None; // no second identifier (a return type) before the name
    }

    const QUALIFIERS: &[&str] = &["in", "out", "inout", "const", "highp", "mediump", "lowp"];
    let mut params = Vec::new();
    for raw_param in params_text.split(',') {
        let words: Vec<&str> = raw_param.split_whitespace().collect();
        let mut idx = 0;
        while idx < words.len() && QUALIFIERS.contains(&words[idx]) {
            idx += 1;
        }
        if idx + 1 >= words.len() {
            continue; // e.g. an empty "(void)"/"()" parameter list
        }
        if let Some(ty) = GlslType::from_keyword(words[idx]) {
            let name: String = words[idx + 1]
                .chars()
                .take_while(|c| crate::golf::is_ident_char(*c))
                .collect();
            if !name.is_empty() {
                params.push((name, ty));
            }
        }
    }
    Some(params)
}

/// Applies the rewrite described in the module docs across `src`, and
/// returns the (possibly unchanged) result.
pub fn fixup_overloaded_matrix_constructors(src: &str) -> String {
    let chars: Vec<char> = src.chars().collect();
    let n = chars.len();
    let mut scopes: Vec<Scope> = vec![Vec::new()];
    let mut replacements: Vec<(usize, usize, String)> = Vec::new();
    let mut i = 0usize;
    // Tracks a `float`/`vecN` declaration in progress so a top-level,
    // comma-separated additional name (`vec2 a = ..., b = ...;`) gets
    // registered with the same type as `a` -- common in real (especially
    // hand-golfed) GLSL. Only consulted/updated outside any `(...)`, so a
    // function-call argument list's own commas never trigger it.
    let mut pending_decl_type: Option<GlslType> = None;
    let mut paren_depth: i32 = 0;

    while i < n {
        let c = chars[i];

        // Comments and preprocessor directives are skipped wholesale --
        // never scanned for identifiers/declarations/constructor calls.
        if c == '/' && i + 1 < n && chars[i + 1] == '/' {
            while i < n && chars[i] != '\n' {
                i += 1;
            }
            continue;
        }
        if c == '/' && i + 1 < n && chars[i + 1] == '*' {
            i += 2;
            while i + 1 < n && !(chars[i] == '*' && chars[i + 1] == '/') {
                i += 1;
            }
            i = (i + 2).min(n);
            continue;
        }
        if c == '#' {
            while i < n && chars[i] != '\n' {
                i += 1;
            }
            continue;
        }

        if c == '{' {
            scopes.push(Vec::new());
            pending_decl_type = None;
            i += 1;
            continue;
        }
        if c == '}' {
            if scopes.len() > 1 {
                scopes.pop();
            }
            pending_decl_type = None;
            i += 1;
            continue;
        }
        if c == '(' || c == '[' {
            paren_depth += 1;
            i += 1;
            continue;
        }
        if c == ')' || c == ']' {
            paren_depth -= 1;
            i += 1;
            continue;
        }
        if c == ';' {
            if paren_depth == 0 {
                pending_decl_type = None;
            }
            i += 1;
            continue;
        }
        if c == ',' && paren_depth == 0 && pending_decl_type.is_some() {
            let mut j = i + 1;
            while j < n && chars[j].is_whitespace() {
                j += 1;
            }
            if j < n && crate::golf::is_ident_start(chars[j]) {
                let name_start = j;
                let mut k = j;
                while k < n && crate::golf::is_ident_char(chars[k]) {
                    k += 1;
                }
                let name: String = chars[name_start..k].iter().collect();
                if let Some(scope) = scopes.last_mut() {
                    scope.push((name, pending_decl_type.unwrap()));
                }
                i = k;
                continue;
            }
            i += 1;
            continue;
        }

        if crate::golf::is_ident_start(c) {
            let start = i;
            while i < n && crate::golf::is_ident_char(chars[i]) {
                i += 1;
            }
            let word: String = chars[start..i].iter().collect();

            let mut j = i;
            while j < n && chars[j].is_whitespace() {
                j += 1;
            }

            if j < n && chars[j] == '(' {
                if let Some(mat_n) = match word.as_str() {
                    "mat2" => Some(2usize),
                    "mat3" => Some(3usize),
                    "mat4" => Some(4usize),
                    _ => None,
                } {
                    // A real constructor call, not a swizzle/member access
                    // masquerading as one (`.mat2(` can't happen -- `.`
                    // isn't an identifier-start char -- but staying
                    // explicit here costs nothing).
                    let open_paren = j;
                    if let Some(close_paren) = find_matching_paren(&chars, open_paren) {
                        let raw_args: String = chars[open_paren + 1..close_paren].iter().collect();
                        let needed = mat_n * mat_n;
                        if let Some(new_args) = rewrite_args(&scopes, &raw_args, needed) {
                            replacements.push((open_paren + 1, close_paren, new_args));
                        }
                        i = close_paren + 1;
                        continue;
                    }
                }
                // Function definition? Extract its parameter list so the
                // upcoming `{` gets the right starting scope.
                if let Some(params) =
                    parse_params_if_function_signature(&chars, j, &{
                        // Re-scan forward for this call's own matching
                        // close paren to get the parameter text -- `j`
                        // above only confirmed an immediate '(' follows.
                        find_matching_paren(&chars, j)
                            .map(|close| chars[j + 1..close].iter().collect::<String>())
                            .unwrap_or_default()
                    })
                {
                    // Stash resolved params on the *next* `{` by pushing
                    // a pre-filled scope now and marking it so the `{`
                    // handler above doesn't push an empty one on top.
                    // Simplest correct approach: just search forward for
                    // the `{` that follows this signature (skipping the
                    // closing `)`), and push there directly.
                    if let Some(close) = find_matching_paren(&chars, j) {
                        let mut k = close + 1;
                        while k < n && chars[k].is_whitespace() {
                            k += 1;
                        }
                        if k < n && chars[k] == '{' {
                            scopes.push(params);
                            i = k + 1;
                            continue;
                        }
                    }
                }
            } else {
                // `TYPE NAME` declaration (local var, struct field, or a
                // parameter list this scanner reaches directly rather
                // than via the function-signature path above -- e.g. the
                // first parameter of a multi-parameter list is found this
                // way too, since `parse_params_if_function_signature`
                // parses the *whole* list at once from the signature's
                // own name token, so no double-counting occurs here for
                // parameters; this path only ever adds plain statement
                // declarations).
                if paren_depth == 0 {
                    if let Some(ty) = GlslType::from_keyword(&word) {
                        if j < n && crate::golf::is_ident_start(chars[j]) {
                            let name_start = j;
                            let mut k = j;
                            while k < n && crate::golf::is_ident_char(chars[k]) {
                                k += 1;
                            }
                            let name: String = chars[name_start..k].iter().collect();
                            if let Some(scope) = scopes.last_mut() {
                                scope.push((name, ty));
                            }
                            pending_decl_type = Some(ty);
                        }
                    }
                }
            }
            continue;
        }

        i += 1;
    }

    if replacements.is_empty() {
        return src.to_string();
    }
    replacements.sort_by_key(|(start, _, _)| *start);
    let mut out = String::with_capacity(src.len());
    let mut cursor = 0usize;
    for (start, end, replacement) in replacements {
        out.extend(chars[cursor..start].iter());
        out.push_str(&replacement);
        cursor = end;
    }
    out.extend(chars[cursor..].iter());
    out
}

/// `open_paren` must index a `(`. Returns the index of its matching `)`,
/// skipping over comments and nested `()`/`[]` (a `matN(...)` argument
/// list can itself contain calls/indexing).
fn find_matching_paren(chars: &[char], open_paren: usize) -> Option<usize> {
    let n = chars.len();
    let mut depth = 0i32;
    let mut i = open_paren;
    while i < n {
        let c = chars[i];
        if c == '/' && i + 1 < n && chars[i + 1] == '/' {
            while i < n && chars[i] != '\n' {
                i += 1;
            }
            continue;
        }
        if c == '/' && i + 1 < n && chars[i + 1] == '*' {
            i += 2;
            while i + 1 < n && !(chars[i] == '*' && chars[i + 1] == '/') {
                i += 1;
            }
            i = (i + 2).min(n);
            continue;
        }
        if c == '(' || c == '[' {
            depth += 1;
        } else if c == ')' || c == ']' {
            depth -= 1;
            if depth == 0 && c == ')' {
                return Some(i);
            }
        }
        i += 1;
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rewrites_the_reported_over_supplied_mat2() {
        let src = "\
float getVal(vec2 U) {
    vec2 c = vec2(0.0), z = vec2(0.0);
    z = mat2(z, -z.y, z)*z + c;
    return z.x;
}
";
        let out = fixup_overloaded_matrix_constructors(src);
        assert!(out.contains("mat2(z, -z.y, (z).x)"), "{out}");
        // Never touches anything else in the file.
        assert!(out.contains("vec2 c = vec2(0.0), z = vec2(0.0);"));
    }

    #[test]
    fn leaves_exact_arity_constructors_untouched() {
        let src = "void f(){ mat2 m = mat2(1.0, 0.0, 0.0, 1.0); }";
        assert_eq!(fixup_overloaded_matrix_constructors(src), src);
    }

    #[test]
    fn leaves_under_supplied_constructors_untouched_for_naga_to_report() {
        let src = "void f(){ vec2 z = vec2(0.0); mat2 m = mat2(z); }";
        assert_eq!(fixup_overloaded_matrix_constructors(src), src);
    }

    #[test]
    fn bails_out_when_a_component_count_is_unresolvable() {
        // `foo()` has no declared/resolvable type -- must not guess.
        let src = "void f(){ mat2 m = mat2(foo(), 1.0, 2.0, 3.0, 4.0); }";
        assert_eq!(fixup_overloaded_matrix_constructors(src), src);
    }

    #[test]
    fn respects_block_scoping_for_same_named_variables() {
        let src = "\
void a() { float z = 1.0; }
void b() {
    vec2 z = vec2(1.0);
    mat2 m = mat2(z, -z.y, z);
}
";
        let out = fixup_overloaded_matrix_constructors(src);
        assert!(out.contains("mat2(z, -z.y, (z).x)"), "{out}");
    }

    #[test]
    fn handles_mat3_and_swizzled_arguments() {
        let src = "\
void f() {
    vec3 a = vec3(1.0);
    vec3 b = vec3(2.0);
    mat3 m = mat3(a, b, a.xyz, b);
}
";
        // needed = 9; a(3)+b(3)+a.xyz(3) = 9 already -- the trailing `b`
        // (3 more) is entirely excess and must be dropped along with its
        // comma.
        let out = fixup_overloaded_matrix_constructors(src);
        assert!(out.contains("mat3(a, b, a.xyz)"), "{out}");
    }

    #[test]
    fn ignores_matrix_constructors_inside_comments() {
        let src = "// mat2(z, -z.y, z) in a comment\nvoid f(){}\n";
        assert_eq!(fixup_overloaded_matrix_constructors(src), src);
    }
}
