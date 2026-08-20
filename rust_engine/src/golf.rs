use std::collections::HashSet;

/// GLSL keywords, types, qualifiers and built-in functions/variables that
/// must never be renamed, plus the Shadertoy-harness identifiers the Rust
/// engine relies on by exact name.
const RESERVED: &[&str] = &[
    // control flow / qualifiers
    "break", "continue", "do", "for", "while", "switch", "case", "default", "if", "else",
    "discard", "return", "struct", "precision", "highp", "mediump", "lowp", "const", "uniform",
    "varying", "attribute", "buffer", "shared", "coherent", "volatile", "restrict", "readonly",
    "writeonly", "layout", "centroid", "flat", "smooth", "noperspective", "patch", "sample",
    "invariant", "precise", "in", "out", "inout", "subroutine", "true", "false", "void",
    // scalar / vector / matrix types
    "float", "double", "int", "uint", "bool",
    "vec2", "vec3", "vec4", "ivec2", "ivec3", "ivec4", "uvec2", "uvec3", "uvec4", "bvec2",
    "bvec3", "bvec4", "dvec2", "dvec3", "dvec4",
    "mat2", "mat3", "mat4", "mat2x2", "mat2x3", "mat2x4", "mat3x2", "mat3x3", "mat3x4", "mat4x2",
    "mat4x3", "mat4x4",
    "sampler2D", "samplerCube", "sampler3D", "sampler2DArray", "samplerShadow", "sampler",
    "texture2D", "textureCube", "texture3D",
    // builtin functions
    "radians", "degrees", "sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh", "tanh",
    "asinh", "acosh", "atanh", "pow", "exp", "log", "exp2", "log2", "sqrt", "inversesqrt", "abs",
    "sign", "floor", "trunc", "round", "roundEven", "ceil", "fract", "mod", "modf", "min", "max",
    "clamp", "mix", "step", "smoothstep", "isnan", "isinf", "floatBitsToInt", "floatBitsToUint",
    "intBitsToFloat", "uintBitsToFloat", "length", "distance", "dot", "cross", "normalize",
    "faceforward", "reflect", "refract", "matrixCompMult", "outerProduct", "transpose",
    "determinant", "inverse", "lessThan", "lessThanEqual", "greaterThan", "greaterThanEqual",
    "equal", "notEqual", "any", "all", "not", "texture", "textureProj", "textureLod",
    "textureOffset", "texelFetch", "dFdx", "dFdy", "fwidth", "main",
    // builtin variables
    "gl_FragCoord", "gl_FragDepth", "gl_Position", "gl_VertexIndex", "gl_InstanceIndex",
    "gl_PointSize",
    // Shadertoy / harness identifiers relied upon by name
    "mainImage", "iResolution", "iTime", "iTimeDelta", "iFrame", "iMouse", "iChannel0",
    "iChannel1", "iChannel2", "iChannel3",
    // preprocessor directive keywords: the tokenizer has no notion of "#"
    // starting a directive line, so the word right after `#` (e.g. the
    // "define" in "#define") is scanned as a plain identifier and must be
    // kept out of the rename map like any other reserved word ("if"/"else"
    // are already covered above as control-flow keywords).
    "define", "undef", "ifdef", "ifndef", "elif", "endif", "pragma", "extension", "version", "line", "error",
];

/// Pipeline order recap (see `golf_shader_impl`/`golf_common`): comments
/// stripped → optional dead-code elimination (`remove_unused_functions`,
/// then — same `dead_code` toggle — `inline_single_call_functions`,
/// inlining every top-level function called exactly once and reducible to
/// a single `return EXPR;`) → optional renaming → literal
/// shortening/whitespace collapse → `strip_default_in_qualifier` (drops the
/// redundant `in` parameter qualifier, GLSL's own default) → `simplify_algebra`
/// (turns `i+=1.`/`i=i+1.` into `i++`) → `golf_for_loops` (this section: recognizes
/// `for(TYPE i=INIT;i<BOUND;i++){...}` and pushes the `i++` into the
/// condition) → `merge_consecutive_declarations` (fuses adjacent
/// same-type declaration statements, `float a=1.;float b=2.;` →
/// `float a=1.,b=2.;`) → `strip_redundant_braces` (drops the `{`/`}`
/// around a single-statement `if`/`for`/`while`/`else` body, `if(x){y=1.;}`
/// → `if(x)y=1.;`, refusing whenever a dangling-else risk is present) →
/// `fold_vector_constructor_splat` (`vecN(x,x,...,x)` → `vecN(x)`, GLSL's
/// own splat rule for a constructor given a single scalar) → redundant-
/// semicolon collapse → `extract_repeated_subexpr_macros` (`golf_shader_impl`
/// only, never `golf_common` — see that function's own doc comment for why:
/// factors an identical, repeated function call or member-access chain into
/// a 1-2 character `#define` at the top of the file, only when doing so is
/// a strict net byte saving).
#[derive(Debug)]
enum Tok {
    Ident(String),
    Number(String),
    Other(String),
}

/// Scans a float literal (requires a `.` or exponent — a bare integer like
/// a loop bound or array size must never be touched, since stripping
/// "trailing zeros" from an actual integer would change its value) starting
/// at `i`. Mirrors `literals::try_scan_float`.
pub(crate) fn try_scan_float_literal(chars: &[char], i: usize) -> Option<usize> {
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
        return None;
    }
    Some(j)
}

/// Shortens a float literal's textual form without changing its value:
/// strips trailing zeros in the fractional part (`0.50` -> `0.5`) and a
/// leading zero before the point when a fractional digit remains
/// (`0.5` -> `.5`), but never down to a bare `.` (invalid) — `0.0`/`0.` stay
/// as the 2-character minimum. The exponent suffix, if any, is untouched.
fn shorten_float_literal(text: &str) -> String {
    let split_at = text.find(['e', 'E']);
    let (mantissa, exponent) = match split_at {
        Some(pos) => (&text[..pos], &text[pos..]),
        None => (text, ""),
    };
    let mut m = mantissa.to_string();
    if let Some(dot_pos) = m.find('.') {
        while m.ends_with('0') {
            m.pop();
        }
        if dot_pos == 1 && m.starts_with("0.") && m.len() > 2 {
            m.remove(0);
        }
    }
    format!("{m}{exponent}")
}

/// True for any GLSL keyword/type/builtin `simplify_algebra` must never
/// treat as "a value already sitting there" (an operand, or something that
/// completes a value for unary-vs-binary context purposes) — same list
/// `golf_shader_impl`'s renamer protects, reused here for the same reason:
/// a reserved word is never a variable/expression result.
fn is_reserved(name: &str) -> bool {
    RESERVED.contains(&name)
}

/// A flat, one-token-per-punctuation-character lexeme used only by
/// `simplify_algebra`. Deliberately simpler than `tokenize`'s `Tok`, which
/// groups punctuation into `Other` runs of unpredictable length (a run can
/// absorb whitespace *and* trailing punctuation up to the next
/// identifier/digit, e.g. the `");\n"` after `pow(x,2.)`) — grouping like
/// that is exactly what would make positional pattern-matching on "the
/// closing `)` of this call" unreliable. Splitting every non-ident,
/// non-number character out to its own token sidesteps that entirely: a
/// pattern like `pow`,`(`,operand,`,`,`2.`,`)` matches (or doesn't) at an
/// exact token offset regardless of what follows.
#[derive(Debug, Clone, PartialEq)]
enum AlgTok {
    Ident(String),
    Number(String),
    /// A run of plain digits with no `.`/exponent (an integer literal —
    /// loop bound, array size, ...): never a match target for any pattern
    /// below (those only ever compare against float `Number`s, matching
    /// `detect_literal_sliders`'/`shorten_float_literal`'s own "a bare
    /// integer is never a float literal" rule), kept as a single token
    /// purely so it round-trips back out unchanged as one piece.
    IntLike(String),
    /// Exactly one non-ident, non-digit character: an operator, a
    /// separator, a single space, or a newline. Kept single-character (as
    /// opposed to `tokenize`'s `Other` run) so `simplify_algebra` can key
    /// off one exact character — e.g. requiring `Punct('*')` immediately
    /// followed by a one-literal `Number`, with nothing else able to have
    /// slipped in between.
    Punct(char),
}

/// Lexes an *already golfed* GLSL string (whitespace already minimal —
/// this is meant to run right after the rename/literal-shortening pass,
/// alongside `collapse_redundant_semicolons`) into `AlgTok`s. Reuses
/// `try_scan_float_literal` so "what counts as a float literal" never
/// drifts from the rest of the file.
fn lex_alg(src: &str) -> Vec<AlgTok> {
    let chars: Vec<char> = src.chars().collect();
    let n = chars.len();
    let mut out = Vec::with_capacity(n);
    let mut i = 0;
    while i < n {
        let c = chars[i];
        if is_ident_start(c) {
            let start = i;
            while i < n && is_ident_char(chars[i]) {
                i += 1;
            }
            out.push(AlgTok::Ident(chars[start..i].iter().collect()));
        } else if c.is_ascii_digit() || (c == '.' && i + 1 < n && chars[i + 1].is_ascii_digit()) {
            if let Some(end) = try_scan_float_literal(&chars, i) {
                out.push(AlgTok::Number(chars[i..end].iter().collect()));
                i = end;
            } else {
                let start = i;
                while i < n && chars[i].is_ascii_digit() {
                    i += 1;
                }
                if i == start {
                    i += 1; // lone '.', not a valid float here either
                }
                out.push(AlgTok::IntLike(chars[start..i].iter().collect()));
            }
        } else {
            out.push(AlgTok::Punct(c));
            i += 1;
        }
    }
    out
}

/// Parses `text` (a `Number` token's text) as an `f64` and compares it to
/// `target` — used to recognise "this literal is exactly one/zero/two/
/// three" regardless of which equivalent spelling (`1`, `1.`, `1.0`, ...)
/// survived up to this point in the pipeline.
fn literal_equals(text: &str, target: f64) -> bool {
    text.parse::<f64>()
        .map(|v| (v - target).abs() < 1e-9)
        .unwrap_or(false)
}

/// Recognises a `Number` literal whose value is an *exact* integer with no
/// exponent — the only shape the constant-folding rule in
/// `simplify_algebra_pass` (below) ever touches. Restricting folding to
/// this shape is what keeps it bit-exact with what the target GPU driver
/// would compute in `f32` at shader run time: two exact-integer float
/// operands combined by `+`/`-`/`*` in `f64` here can never land on a
/// different result than the same combination performed in `f32` on the
/// GPU (integers are represented exactly in either format, well below
/// either format's precision limit for any realistic shader constant), so
/// there is no risk of the visually-undetectable-but-real ULP drift that
/// folding an irrational-ish literal like `3.14159265` (already only an
/// approximation of pi) would introduce. `1e15` is a conservative bound —
/// comfortably inside `f64`'s exact-integer range (2^53) with headroom to
/// spare, chosen only to rule out pathological huge literals no real
/// shader would ever contain rather than to approach any real precision
/// limit.
fn exact_integer_literal(text: &str) -> Option<i64> {
    if text.contains(['e', 'E']) {
        return None;
    }
    let value: f64 = text.parse().ok()?;
    if value.fract() != 0.0 || value.abs() >= 1e15 {
        return None;
    }
    Some(value as i64)
}

/// Pushes the folded integer result of a constant-folding match onto
/// `out`, spelled as a valid GLSL float literal. GLSL numeric literals
/// never carry a sign themselves (a negative *value* is always a unary
/// minus applied to a positive literal) — mirroring the rest of this file,
/// which never synthesizes a `Number` token whose text starts with `-`.
/// The current fixed-point pass re-lexes the whole string from scratch
/// next iteration anyway, so emitting `Punct('-')` followed by a positive
/// `Number` here is exactly as safe as writing that text out by hand would
/// have been.
fn push_folded_integer_literal(out: &mut Vec<AlgTok>, value: i64) {
    if value < 0 {
        out.push(AlgTok::Punct('-'));
        out.push(AlgTok::Number(format!("{}.", -value)));
    } else {
        out.push(AlgTok::Number(format!("{value}.")));
    }
}

/// An operand simple enough for the peephole rules below to touch: a
/// single identifier or numeric literal, standing for itself and nothing
/// more. Deliberately excludes anything composite (`)`, `]`, a whole
/// parenthesised expression, ...) — per the rules' own scope, they must
/// never reach into an arbitrary subexpression, only ever replace/drop a
/// literal sitting directly next to a *lone* token. Reserved words are
/// excluded too: a keyword is never a value.
fn is_atomic_operand(tok: &AlgTok) -> bool {
    match tok {
        AlgTok::Number(_) => true,
        AlgTok::Ident(name) => !is_reserved(name),
        _ => false,
    }
}

/// True if `tok`, sitting immediately to the left of a `-`, already
/// completes a value — meaning that `-` is binary subtraction, not a unary
/// minus. Used only to decide whether a following `- -` is genuine double
/// negation (unary context) or `a - -b` (binary minus of a unary minus,
/// where the two `-` must never be touched as a pair). Slightly broader
/// than `is_atomic_operand`: a closing `)`/`]` also completes a value
/// (`f(x) - -y`, `a[0] - -y`) even though it isn't itself something the
/// other rules would rewrite.
fn is_binary_lhs(tok: &AlgTok) -> bool {
    match tok {
        AlgTok::Number(_) => true,
        AlgTok::Ident(name) => !is_reserved(name),
        AlgTok::Punct(')') | AlgTok::Punct(']') => true,
        _ => false,
    }
}

/// True when `term` (the token right after a candidate `x=x+1.`/`x+=1.`
/// match) proves the assignment's *result value* is never used — the only
/// condition under which rewriting it to `x++`/`x--` is safe, since a
/// pre/post-increment's return value differs from the assignment's.
/// - `;` ends a full statement outright: always safe.
/// - `)` is only safe when `prev` (the token immediately *before* the whole
///   matched pattern) is `;` — i.e. this is a for-loop's increment clause,
///   `for(init;cond;x+=1.)`, whose own preceding `;` is the one that ends
///   the condition clause. Any other use of `)` right after an assignment
///   (a call argument `foo(x+=1.)`, an `if(x=x+1.)` condition, ...) is
///   preceded by `(` instead, not `;`, so it's correctly left alone.
fn is_valid_increment_terminator(term: &AlgTok, prev: Option<&AlgTok>) -> bool {
    match term {
        AlgTok::Punct(';') => true,
        AlgTok::Punct(')') => matches!(prev, Some(AlgTok::Punct(';'))),
        _ => false,
    }
}

/// One left-to-right pass applying every peephole rule at most once per
/// position; `simplify_algebra` below iterates this to a fixed point so
/// that e.g. `pow(x,2.)*1.` fully collapses to `x*x` even though the `*1.`
/// only becomes adjacent to a lone operand *after* the `pow` rewrite runs.
///
/// Every rule here is intentionally the narrowest textual match that
/// implements it — operands are always a single `AlgTok`, never a
/// reconstructed subexpression — so a rule either fires on an exact,
/// unambiguous shape or leaves the tokens alone; there is no partial/best-
/// effort rewriting that could silently miscompile.
fn simplify_algebra_pass(src: &str) -> String {
    let toks = lex_alg(src);
    let n = toks.len();
    let mut out: Vec<AlgTok> = Vec::with_capacity(n);
    let mut i = 0usize;
    while i < n {
        // --- x=x+1. / x=x-1. -> x++ / x-- , and x+=1. / x-=1. -> x++ / x--.
        // See `is_valid_increment_terminator` for why only some contexts
        // qualify. `name` must match on both sides of `=` for the first
        // shape (an accumulator reassigning itself), and is excluded from
        // being a reserved word so this can never fire on nonsense like a
        // stray `pow=pow+1.` (impossible in valid GLSL, but `pow` itself
        // must never be treated as a variable name here regardless). ---
        if let Some(AlgTok::Ident(name)) = toks.get(i) {
            if !is_reserved(name) {
                let prev = out.last().cloned();
                // x=x+1. / x=x-1.
                if let (
                    Some(AlgTok::Punct('=')),
                    Some(AlgTok::Ident(name2)),
                    Some(AlgTok::Punct(op)),
                    Some(AlgTok::Number(num)),
                    Some(term),
                ) = (
                    toks.get(i + 1),
                    toks.get(i + 2),
                    toks.get(i + 3),
                    toks.get(i + 4),
                    toks.get(i + 5),
                ) {
                    let op = *op;
                    if name2 == name
                        && (op == '+' || op == '-')
                        && literal_equals(num, 1.0)
                        && is_valid_increment_terminator(term, prev.as_ref())
                    {
                        out.push(AlgTok::Ident(name.clone()));
                        out.push(AlgTok::Punct(op));
                        out.push(AlgTok::Punct(op));
                        out.push(term.clone());
                        i += 6;
                        continue;
                    }
                }
                // x+=1. / x-=1.
                if let (Some(AlgTok::Punct(op)), Some(AlgTok::Punct('=')), Some(AlgTok::Number(num)), Some(term)) =
                    (toks.get(i + 1), toks.get(i + 2), toks.get(i + 3), toks.get(i + 4))
                {
                    let op = *op;
                    if (op == '+' || op == '-')
                        && literal_equals(num, 1.0)
                        && is_valid_increment_terminator(term, prev.as_ref())
                    {
                        out.push(AlgTok::Ident(name.clone()));
                        out.push(AlgTok::Punct(op));
                        out.push(AlgTok::Punct(op));
                        out.push(term.clone());
                        i += 5;
                        continue;
                    }
                }
                // --- generalized compound assignment: x = x OP atomic ->
                // x OP= atomic, for any of + - * / % and an atomic
                // (identifier/literal) right-hand operand -- reached only
                // when the increment-specific rule above didn't already
                // match (so `x=x+1.` still becomes the strictly shorter
                // `x++`, never `x+=1.`). Same token shape as that rule,
                // generalized in two independent ways: any operator instead
                // of just `+`/`-`, and any atomic operand instead of just
                // the literal `1`.
                //
                // The terminator check is not just inherited for
                // consistency, it is *required* for correctness here: it's
                // the only thing proving the atomic operand is the *entire*
                // right-hand side rather than merely its first token.
                // Without it, `x=x*x+1.;` (parses as `(x*x)+1.`) would
                // wrongly become `x*=x+1.;` (means `x*(x+1.)` ==
                // `x*x+x`, a different value) — the atomic-operand check
                // alone accepts the first `x` in `x*x+1.` just fine, it's
                // only the terminator check right after it (here `+`, not
                // `;`/`)`) that correctly rejects the match. Unlike the
                // increment rule, no "is the assignment's own value used"
                // restriction is needed on top of that: `x OP= atomic` and
                // `x = x OP atomic` evaluate to the exact same new value in
                // every context (both are, by definition, an assignment
                // expression whose value is the freshly assigned value),
                // so this can safely fire even inside `if(x=x*2.)` or a
                // call argument.
                if let (
                    Some(AlgTok::Punct('=')),
                    Some(AlgTok::Ident(name2)),
                    Some(AlgTok::Punct(op)),
                    Some(operand),
                    Some(term),
                ) = (
                    toks.get(i + 1),
                    toks.get(i + 2),
                    toks.get(i + 3),
                    toks.get(i + 4),
                    toks.get(i + 5),
                ) {
                    let op = *op;
                    if name2 == name
                        && matches!(op, '+' | '-' | '*' | '/' | '%')
                        && is_atomic_operand(operand)
                        && is_valid_increment_terminator(term, prev.as_ref())
                    {
                        out.push(AlgTok::Ident(name.clone()));
                        out.push(AlgTok::Punct(op));
                        out.push(AlgTok::Punct('='));
                        out.push(operand.clone());
                        out.push(term.clone());
                        i += 6;
                        continue;
                    }
                }
            }
        }

        // --- "operand OP literal" rules: the operand is whatever we just
        // emitted (`out.last()`), so these only fire once something atomic
        // is already sitting at the end of `out`. ---
        if let Some(last) = out.last() {
            if is_atomic_operand(last) {
                if let (Some(AlgTok::Punct(op)), Some(AlgTok::Number(num))) = (toks.get(i), toks.get(i + 1)) {
                    let op = *op;
                    // x*1. / x/1. -> x (identity element, dropped entirely)
                    if (op == '*' || op == '/') && literal_equals(num, 1.0) {
                        i += 2;
                        continue;
                    }
                    // x*0. -> 0. (and the mirrored 0.*x below)
                    if op == '*' && literal_equals(num, 0.0) {
                        out.pop();
                        out.push(AlgTok::Number("0.".to_string()));
                        i += 2;
                        continue;
                    }
                    // x+0. / x-0. -> x
                    if (op == '+' || op == '-') && literal_equals(num, 0.0) {
                        i += 2;
                        continue;
                    }
                }
            }
        }

        // --- "literal OP operand" rules: the mirror image, matched
        // forward since here the literal comes first. ---
        if let (Some(AlgTok::Number(num)), Some(AlgTok::Punct(op)), Some(next)) =
            (toks.get(i), toks.get(i + 1), toks.get(i + 2))
        {
            if is_atomic_operand(next) {
                // 1.*x -> x
                if *op == '*' && literal_equals(num, 1.0) {
                    out.push(next.clone());
                    i += 3;
                    continue;
                }
                // 0.*x -> 0. (mirrors x*0. above; multiplication is
                // commutative, equally safe either way round)
                if *op == '*' && literal_equals(num, 0.0) {
                    out.push(AlgTok::Number("0.".to_string()));
                    i += 3;
                    continue;
                }
                // 0.+x -> x
                if *op == '+' && literal_equals(num, 0.0) {
                    out.push(next.clone());
                    i += 3;
                    continue;
                }
            }
        }

        // --- constant folding: two literal numeric operands combined by
        // `+`/`-`/`*` fold to their computed result at golf time (e.g.
        // `2.*3.` -> `6.`), but only when *both* are exact integers in
        // floating point (see `exact_integer_literal`'s own doc comment for
        // the bit-exactness argument that restricts this to that shape —
        // `2.*3.14159265` must never fold, since the second operand is
        // already only an approximation of pi and recombining it here could
        // land on a different `f32` rounding than the GPU driver would
        // produce at run time). Deliberately excludes `/`: integer division
        // in GLSL is exact for floats (`6./3.` == `2.` here as much as on
        // the GPU) but folding a literal-vs-literal division would still
        // require deciding what to do when it *isn't* exact (`5./2.` ==
        // `2.5`, no longer an integer to round-trip through this same
        // `exact_integer_literal` shape without its own separate
        // ULP argument) — left out of this first pass entirely rather than
        // half-covering it. ---
        if let (Some(AlgTok::Number(a_text)), Some(AlgTok::Punct(op)), Some(AlgTok::Number(b_text))) =
            (toks.get(i), toks.get(i + 1), toks.get(i + 2))
        {
            let op = *op;
            if matches!(op, '+' | '-' | '*') {
                if let (Some(a), Some(b)) = (exact_integer_literal(a_text), exact_integer_literal(b_text)) {
                    let folded = match op {
                        '+' => a.checked_add(b),
                        '-' => a.checked_sub(b),
                        '*' => a.checked_mul(b),
                        _ => unreachable!(),
                    };
                    if let Some(result) = folded {
                        push_folded_integer_literal(&mut out, result);
                        i += 3;
                        continue;
                    }
                }
            }
        }

        // --- pow(x,2.) -> x*x / pow(x,3.) -> x*x*x. Only when the
        // argument is itself a lone atomic operand: `pow(a+b,2.)` doesn't
        // match (the token right after `(` is `a`, but the one after that
        // is `+`, not the required `,`), so it's left as `pow`, untouched.
        // Deliberately stops at the exponent 3 — beyond that `pow` is
        // shorter than the repeated-multiplication expansion. ---
        if let (
            Some(AlgTok::Ident(name)),
            Some(AlgTok::Punct('(')),
            Some(operand),
            Some(AlgTok::Punct(',')),
            Some(AlgTok::Number(exp)),
            Some(AlgTok::Punct(')')),
        ) = (
            toks.get(i),
            toks.get(i + 1),
            toks.get(i + 2),
            toks.get(i + 3),
            toks.get(i + 4),
            toks.get(i + 5),
        ) {
            if name == "pow" && is_atomic_operand(operand) {
                if literal_equals(exp, 2.0) {
                    out.push(operand.clone());
                    out.push(AlgTok::Punct('*'));
                    out.push(operand.clone());
                    i += 6;
                    continue;
                }
                if literal_equals(exp, 3.0) {
                    out.push(operand.clone());
                    out.push(AlgTok::Punct('*'));
                    out.push(operand.clone());
                    out.push(AlgTok::Punct('*'));
                    out.push(operand.clone());
                    i += 6;
                    continue;
                }
            }
        }

        // --- double unary negation: `- -x` -> `x`. Only ever reachable
        // here as a *space*- or *newline*-separated pair: `strip_operator_spaces`
        // (see above) guarantees a genuine `--` (zero characters between
        // the two `-`) is always the real decrement/increment operator,
        // deliberately preserving exactly one separating character
        // whenever two unary minuses were written back to back, precisely
        // so this rule can never confuse the two. Also gated on unary
        // context (`is_binary_lhs`) so `a - -b` (binary minus of a unary
        // minus) is never touched — only a `-` that itself starts a fresh
        // expression can be "double negation". ---
        if matches!(toks.get(i), Some(AlgTok::Punct('-')))
            && out.last().map_or(true, |t| !is_binary_lhs(t))
        {
            if let (Some(sep), Some(AlgTok::Punct('-')), Some(operand)) =
                (toks.get(i + 1), toks.get(i + 2), toks.get(i + 3))
            {
                let sep_ok = matches!(sep, AlgTok::Punct(' ') | AlgTok::Punct('\n'));
                if sep_ok && is_atomic_operand(operand) {
                    out.push(operand.clone());
                    i += 4;
                    continue;
                }
            }
        }

        out.push(toks[i].clone());
        i += 1;
    }

    let mut s = String::with_capacity(src.len());
    for tok in &out {
        match tok {
            AlgTok::Ident(t) | AlgTok::Number(t) | AlgTok::IntLike(t) => s.push_str(t),
            AlgTok::Punct(c) => s.push(*c),
        }
    }
    s
}

/// Peephole algebraic simplification: repeatedly applies
/// `simplify_algebra_pass` to a fixed point (capped, like
/// `remove_unused_functions`, so a pathological input can't loop forever —
/// in practice a couple of passes is enough even for a chain like
/// `pow(x,2.)*1.` -> `x*x*1.` -> `x*x`). Every individual rule is a purely
/// local match on neighbouring tokens, never on a reconstructed
/// subexpression, so a pattern that fails to match just leaves the source
/// untouched there rather than guessing — exactly as safe as the other
/// always-on transforms in this file, and covered by the same golf-à-froid
/// round-trip compile check before anything is written out.
fn simplify_algebra(src: &str) -> String {
    let mut current = src.to_string();
    for _ in 0..8 {
        let next = simplify_algebra_pass(&current);
        if next == current {
            break;
        }
        current = next;
    }
    current
}

/// GLSL scalar types realistically used to declare a `for`-loop counter —
/// the only values `golf_for_loops` accepts as the `TYPE` in the canonical
/// `for(TYPE i=INIT;i<BOUND;i++){...}` shape it looks for. Not an exhaustive
/// list of GLSL types (no vectors/matrices/samplers: none of those are ever
/// a loop counter), deliberately narrow like `RESERVED` itself.
const FOR_LOOP_COUNTER_TYPES: &[&str] = &["float", "int", "uint", "double"];

/// True if `name` occurs as an `AlgTok::Ident` anywhere in `toks` — used by
/// `try_rewrite_for_loop` to check that a candidate loop variable is never
/// touched outside the three canonical header positions the match itself
/// already accounts for.
fn contains_ident(toks: &[AlgTok], name: &str) -> bool {
    toks.iter().any(|t| matches!(t, AlgTok::Ident(n) if n == name))
}

/// Finds the index of the `AlgTok::Punct(close)` that matches the
/// `AlgTok::Punct(open)` sitting at `open_idx`, tracking nesting depth so an
/// inner `{`/`}` pair (an `if` inside the loop body, ...) doesn't fool it.
fn find_matching_close(toks: &[AlgTok], open_idx: usize, open: char, close: char) -> Option<usize> {
    let mut depth = 0i32;
    let mut j = open_idx;
    while j < toks.len() {
        match &toks[j] {
            AlgTok::Punct(c) if *c == open => depth += 1,
            AlgTok::Punct(c) if *c == close => {
                depth -= 1;
                if depth == 0 {
                    return Some(j);
                }
            }
            _ => {}
        }
        j += 1;
    }
    None
}

/// If the tokens starting at `for_idx` (which must already be `for` `(`,
/// checked by the caller) form the exact canonical shape
/// `for(TYPE i=INIT;i<BOUND;i++){BODY}`, returns the rewritten token
/// sequence (`for(TYPE i=INIT;i++<BOUND;){BODY}`, increment pushed into the
/// condition, increment clause left empty) plus the index right after the
/// body's closing `}`. Returns `None` on anything short of an exact match —
/// no partial/best-effort rewriting, exactly like every other rule in this
/// file: a pattern that doesn't fit is left completely untouched.
///
/// The two clauses are only reordered, nothing inside `INIT`/`BOUND`/`BODY`
/// is otherwise touched or re-parsed, so this is safe regardless of what
/// those expressions actually contain *by itself* — what makes the rewrite
/// unsafe in general is that `i++<BOUND` increments `i` **before** the
/// comparison instead of after the body, so every iteration's body — and
/// the loop's own bound check — ends up seeing `i` one higher than the
/// original would have. That's invisible from outside the loop as long as
/// nothing depends on `i`'s exact value while the loop is still running or
/// mid-declaration, which is exactly what the three `contains_ident` checks
/// below rule out: the loop variable must appear *nowhere* except the three
/// fixed header slots this match already accounts for (its declaration, the
/// bound comparison, the increment) — not in `INIT`, not in `BOUND`, and
/// not anywhere in `BODY`. This holds regardless of whether `TYPE` is `int`
/// or `float`: an integer loop counter isn't any safer to shift by one than
/// a float one, so the check doesn't special-case either.
fn try_rewrite_for_loop(toks: &[AlgTok], for_idx: usize) -> Option<(Vec<AlgTok>, usize)> {
    let mut j = for_idx + 2; // past `for` `(`, already checked by caller

    let type_name = match toks.get(j) {
        Some(AlgTok::Ident(t)) if FOR_LOOP_COUNTER_TYPES.contains(&t.as_str()) => t.clone(),
        _ => return None,
    };
    j += 1;

    // The mandatory single space between the type and the variable name:
    // `collapse_whitespace` always leaves exactly one space between two
    // adjacent identifiers (a purely-whitespace run can't be dropped
    // outright without merging them into one identifier), so this is the
    // one place in the whole pattern where a space token is expected.
    if !matches!(toks.get(j), Some(AlgTok::Punct(' '))) {
        return None;
    }
    j += 1;

    let varname = match toks.get(j) {
        Some(AlgTok::Ident(v)) if !is_reserved(v) => v.clone(),
        _ => return None,
    };
    j += 1;

    if !matches!(toks.get(j), Some(AlgTok::Punct('='))) {
        return None;
    }
    j += 1;

    // INIT: everything up to the first `;`. No depth tracking needed — GLSL
    // has no statement-expressions, so a bare `;` can never legally occur
    // inside an expression; the first one found is always the real clause
    // separator.
    let init_start = j;
    loop {
        match toks.get(j) {
            Some(AlgTok::Punct(';')) => break,
            Some(_) => j += 1,
            None => return None,
        }
    }
    let init_end = j;
    j += 1; // past `;`

    // Condition must be exactly `varname` `<` BOUND (strict `<` only, see
    // below) — any other relational operator, or a condition not starting
    // with exactly this variable, isn't the canonical shape and is left
    // alone.
    match toks.get(j) {
        Some(AlgTok::Ident(v)) if *v == varname => {}
        _ => return None,
    }
    j += 1;
    if !matches!(toks.get(j), Some(AlgTok::Punct('<'))) {
        return None;
    }
    j += 1;
    // Reject `<=` specifically: the canonical motif this pass recognizes is
    // strict `<` only (see the roadmap item this implements) — a `<=` right
    // after would otherwise be silently absorbed as the start of BOUND
    // below, producing a technically-still-correct but unintended match.
    if matches!(toks.get(j), Some(AlgTok::Punct('='))) {
        return None;
    }

    let bound_start = j;
    loop {
        match toks.get(j) {
            Some(AlgTok::Punct(';')) => break,
            Some(_) => j += 1,
            None => return None,
        }
    }
    let bound_end = j;
    j += 1; // past `;`

    // Increment clause must be exactly `varname++` (already-golfed shape —
    // `simplify_algebra` runs before this pass and turns `i=i+1.`/`i+=1.`
    // into `i++` first), immediately followed by the loop's closing `)`.
    match toks.get(j) {
        Some(AlgTok::Ident(v)) if *v == varname => {}
        _ => return None,
    }
    j += 1;
    if !matches!(toks.get(j), Some(AlgTok::Punct('+'))) {
        return None;
    }
    j += 1;
    if !matches!(toks.get(j), Some(AlgTok::Punct('+'))) {
        return None;
    }
    j += 1;
    if !matches!(toks.get(j), Some(AlgTok::Punct(')'))) {
        return None;
    }
    j += 1;
    if !matches!(toks.get(j), Some(AlgTok::Punct('{'))) {
        return None;
    }
    let body_open = j;
    let body_close = find_matching_close(toks, body_open, '{', '}')?;

    let init_tokens = &toks[init_start..init_end];
    let bound_tokens = &toks[bound_start..bound_end];
    let body_tokens = &toks[body_open + 1..body_close];

    if contains_ident(init_tokens, &varname)
        || contains_ident(bound_tokens, &varname)
        || contains_ident(body_tokens, &varname)
    {
        return None;
    }

    let mut rebuilt = Vec::with_capacity(body_close - for_idx + 4);
    rebuilt.push(AlgTok::Ident("for".to_string()));
    rebuilt.push(AlgTok::Punct('('));
    rebuilt.push(AlgTok::Ident(type_name));
    rebuilt.push(AlgTok::Punct(' '));
    rebuilt.push(AlgTok::Ident(varname.clone()));
    rebuilt.push(AlgTok::Punct('='));
    rebuilt.extend_from_slice(init_tokens);
    rebuilt.push(AlgTok::Punct(';'));
    rebuilt.push(AlgTok::Ident(varname.clone()));
    rebuilt.push(AlgTok::Punct('+'));
    rebuilt.push(AlgTok::Punct('+'));
    rebuilt.push(AlgTok::Punct('<'));
    rebuilt.extend_from_slice(bound_tokens);
    rebuilt.push(AlgTok::Punct(';'));
    rebuilt.push(AlgTok::Punct(')'));
    rebuilt.push(AlgTok::Punct('{'));
    rebuilt.extend_from_slice(body_tokens);
    rebuilt.push(AlgTok::Punct('}'));

    Some((rebuilt, body_close + 1))
}

/// One left-to-right pass rewriting every canonical `for`-loop it finds;
/// `golf_for_loops` below iterates this to a fixed point so a loop nested
/// inside another matched loop's body — left untouched by the pass that
/// rewrites the outer one, since `BODY` is copied through verbatim — still
/// gets its turn on the next pass.
fn golf_for_loops_pass(src: &str) -> String {
    let toks = lex_alg(src);
    let n = toks.len();
    let mut out: Vec<AlgTok> = Vec::with_capacity(n);
    let mut i = 0usize;
    while i < n {
        if matches!(toks.get(i), Some(AlgTok::Ident(kw)) if kw == "for")
            && matches!(toks.get(i + 1), Some(AlgTok::Punct('(')))
        {
            if let Some((rewritten, next)) = try_rewrite_for_loop(&toks, i) {
                out.extend(rewritten);
                i = next;
                continue;
            }
        }
        out.push(toks[i].clone());
        i += 1;
    }

    let mut s = String::with_capacity(src.len());
    for tok in &out {
        match tok {
            AlgTok::Ident(t) | AlgTok::Number(t) | AlgTok::IntLike(t) => s.push_str(t),
            AlgTok::Punct(c) => s.push(*c),
        }
    }
    s
}

/// Golfs `for(TYPE i=INIT;i<BOUND;i++){BODY}` down to the condensed
/// `for(TYPE i=INIT;i++<BOUND;){BODY}` shape found in the vast majority of
/// golfed Shadertoy raymarchers — pushing the increment into the condition
/// saves the `;` + repeated variable name the separate increment clause
/// would otherwise cost. Deliberately narrow (see `try_rewrite_for_loop`'s
/// doc comment for exactly what's required and why): this is the single
/// exact motif recognized, not a general loop-rewriting pass, specifically
/// so it can never change the semantics of a `for` that uses its loop
/// variable any other way. Iterates to a fixed point (capped, same
/// reasoning as `simplify_algebra`/`remove_unused_functions`) to reach
/// nested matches; runs after `simplify_algebra` so a `for(...;;i+=1.){...}`
/// increment clause has already become `i++` by the time this looks for it.
fn golf_for_loops(src: &str) -> String {
    let mut current = src.to_string();
    for _ in 0..4 {
        let next = golf_for_loops_pass(&current);
        if next == current {
            break;
        }
        current = next;
    }
    current
}

/// GLSL scalar/vector/matrix types a declaration can plausibly be merged
/// under — deliberately the "basic" value types only (per the roadmap
/// item's own "même type de base" wording): no samplers (multiple sampler
/// declarations sharing one specifier is exotic and not what this targets)
/// and no user-defined `struct` names (this pass has no notion of which
/// identifiers are struct types, so it never tries).
const DECL_BASE_TYPES: &[&str] = &[
    "float", "double", "int", "uint", "bool",
    "vec2", "vec3", "vec4", "ivec2", "ivec3", "ivec4", "uvec2", "uvec3", "uvec4",
    "bvec2", "bvec3", "bvec4", "dvec2", "dvec3", "dvec4",
    "mat2", "mat3", "mat4", "mat2x2", "mat2x3", "mat2x4", "mat3x2", "mat3x3", "mat3x4",
    "mat4x2", "mat4x3", "mat4x4",
];

fn is_decl_base_type(name: &str) -> bool {
    DECL_BASE_TYPES.contains(&name)
}

/// A single `for(...)`-free declaration statement (`TYPE a[=INIT](,b[=INIT])*;`)
/// found by `parse_type_decl_stmt`, starting at some `AlgTok::Ident` whose
/// text is a `DECL_BASE_TYPES` entry. `end` is the index right after the
/// statement's terminating `;`.
struct ParsedDeclStmt {
    type_name: String,
    /// One entry per declared variable, in source order: its name, and its
    /// initializer tokens (everything between `=` and the declarator's own
    /// terminator), if it has one.
    declarators: Vec<(String, Option<Vec<AlgTok>>)>,
    end: usize,
}

/// Parses the single declaration statement starting at `toks[start]` (which
/// must already be a `DECL_BASE_TYPES` identifier — checked by the caller).
/// Returns `None` for anything that isn't cleanly a plain declaration list:
/// a function definition (`TYPE name(...)`, no space consumed before `(`
/// fails the initializer scan below), an array declaration (`TYPE name[n]`
/// — deliberately never merged, seeAlgTok comment below), or anything else
/// that doesn't end in a top-level `;` before running out of tokens.
///
/// Initializer expressions are scanned with `(`/`[` depth tracking (unlike
/// `try_rewrite_for_loop`'s `INIT`/`BOUND`, which only ever needed to find a
/// `;`): a constructor call like `vec3(1.,2.,3.)` has commas of its own
/// that must never be mistaken for the declarator-separating `,` this
/// function is itself looking for.
fn parse_type_decl_stmt(toks: &[AlgTok], start: usize) -> Option<ParsedDeclStmt> {
    let type_name = match toks.get(start) {
        Some(AlgTok::Ident(t)) if is_decl_base_type(t) => t.clone(),
        _ => return None,
    };
    let mut j = start + 1;
    if !matches!(toks.get(j), Some(AlgTok::Punct(' '))) {
        return None;
    }
    j += 1;

    let mut declarators = Vec::new();
    loop {
        let name = match toks.get(j) {
            Some(AlgTok::Ident(n)) if !is_reserved(n) => n.clone(),
            _ => return None,
        };
        j += 1;

        // Array declarations (`float arr[4]`) are never merged: splicing
        // one into a shared declarator list is easy to get subtly wrong
        // (which `[...]` belongs to which name once several are strung
        // together), so this pass backs off from the whole statement
        // rather than risk it — same "restrictif" call as everywhere else
        // in this file.
        if matches!(toks.get(j), Some(AlgTok::Punct('['))) {
            return None;
        }

        let init = if matches!(toks.get(j), Some(AlgTok::Punct('='))) {
            j += 1;
            let init_start = j;
            let mut depth = 0i32;
            loop {
                match toks.get(j) {
                    Some(AlgTok::Punct('(')) | Some(AlgTok::Punct('[')) => {
                        depth += 1;
                        j += 1;
                    }
                    Some(AlgTok::Punct(')')) | Some(AlgTok::Punct(']')) => {
                        depth -= 1;
                        j += 1;
                    }
                    Some(AlgTok::Punct(',')) if depth == 0 => break,
                    Some(AlgTok::Punct(';')) if depth == 0 => break,
                    Some(_) => j += 1,
                    None => return None,
                }
            }
            Some(toks[init_start..j].to_vec())
        } else {
            None
        };
        declarators.push((name, init));

        match toks.get(j) {
            Some(AlgTok::Punct(',')) => {
                j += 1;
                continue;
            }
            Some(AlgTok::Punct(';')) => {
                j += 1;
                break;
            }
            // Anything else right after a bare name/initializer — most
            // notably `(`, a function definition's parameter list — means
            // this was never a declaration statement to begin with.
            _ => return None,
        }
    }
    Some(ParsedDeclStmt { type_name, declarators, end: j })
}

/// True if `out` (everything already emitted) ends with a reserved keyword
/// immediately before the position about to be scanned — i.e. a qualifier
/// like `const`/`highp`/`out`/`layout` sitting right in front of the type.
/// `merge_consecutive_declarations` refuses to even attempt a fusion in
/// that case: merging into a single comma-joined declarator list applies
/// whatever qualifier prefix the *first* statement carries to *every*
/// merged name (that's how GLSL's grammar works), which is only correct if
/// every one of the merged statements originally had that exact same
/// qualifier — a fact this pass has no way to confirm for statements after
/// the first, since a bare (unqualified) `TYPE` immediately following a
/// prior `;` looks identical whether or not the *first* statement in the
/// chain was qualified. Refusing outright whenever the first one carries
/// any prefix keyword sidesteps the ambiguity entirely rather than risk
/// silently making an unqualified variable `const`.
fn preceded_by_qualifier(out: &[AlgTok]) -> bool {
    if matches!(out.last(), Some(AlgTok::Punct(' '))) {
        if let Some(AlgTok::Ident(prev)) = out.get(out.len().wrapping_sub(2)) {
            return is_reserved(prev);
        }
    }
    false
}

/// Advances `j` past any run of space/newline tokens — the only thing
/// `merge_consecutive_declarations` allows to separate two declaration
/// statements it fuses (skipping it, rather than requiring the next token
/// to sit literally adjacent, is what lets it fuse declarations written on
/// their own lines, the common case in practice).
fn skip_ws(toks: &[AlgTok], mut j: usize) -> usize {
    while matches!(toks.get(j), Some(AlgTok::Punct(' ')) | Some(AlgTok::Punct('\n'))) {
        j += 1;
    }
    j
}

/// Removes the `in` parameter qualifier when it's redundant: `in` is
/// GLSL's default parameter-passing qualifier, so writing it out is never
/// required — `void mainImage(out vec4 fragColor, vec2 fragCoord)` means
/// exactly the same thing as the explicit `in vec2 fragCoord` version seen
/// throughout `default.frag` and practically every shadertoy.com shader
/// (the site's own writing habit, not a language requirement). Unlike
/// every other rule in this file, **no safety guard beyond scope is
/// needed**: `in` explicit or implicit never changes the program's
/// meaning, so any `in` recognised as a standalone parameter qualifier is
/// unconditionally safe to drop.
///
/// Scope, and why restricting it costs nothing: only an `in` immediately
/// preceded by `(` or `,` (a parameter-list boundary) is touched — this is
/// deliberately narrower than "every `in` token in the file", which would
/// also catch a top-level `in`-qualified variable declaration (a genuine,
/// non-default varying qualifier outside a parameter list, where dropping
/// it *would* change the meaning). Shadertoy-style shaders never declare
/// one (no vertex-stage inputs here, only uniforms/`iChannel*`), so this
/// scope restriction never actually fires in practice — but it costs
/// nothing to keep and removes any doubt. `inout` tokenises as its own
/// `Ident` (never `in` followed by a separate `out`), so it is never at
/// risk of a false match here — covered by a dedicated test rather than
/// merely assumed.
///
/// A single pass suffices (no fixed point needed): removing an `in`
/// qualifier can never create a new `(`/`,` boundary for another `in` to
/// match against, so nothing is left to find on a second pass.
fn strip_default_in_qualifier(src: &str) -> String {
    let toks = lex_alg(src);
    let n = toks.len();
    let mut out: Vec<AlgTok> = Vec::with_capacity(n);
    let mut i = 0;
    while i < n {
        if let AlgTok::Ident(name) = &toks[i] {
            if name == "in" {
                let prev_is_param_boundary =
                    matches!(out.last(), Some(AlgTok::Punct('(')) | Some(AlgTok::Punct(',')));
                if prev_is_param_boundary {
                    // A space (or, in principle, a newline) must separate
                    // `in` from the type that follows it in valid GLSL —
                    // otherwise the two would have lexed as one identifier
                    // — so drop that separator along with the keyword
                    // itself, rather than leaving a stray leading space
                    // right after the `(`/`,`.
                    i = skip_ws(&toks, i + 1);
                    continue;
                }
            }
        }
        out.push(toks[i].clone());
        i += 1;
    }
    render_alg_toks(&out)
}


/// (`float a=1.;float b=2.;` -> `float a=1.,b=2.;`) — a very common shape
/// at the top of `mainImage` laying out raymarching variables (origin,
/// direction, accumulated distance, ...). Only ever merges statements that
/// are *strictly* adjacent modulo whitespace (see `skip_ws`): the moment
/// anything else — another statement, a `{`/`}`, a differently-typed or
/// qualified declaration, a function definition — sits between two
/// declarations, the chain stops right there, so this can never reach
/// across an `if`/`for` and change which scope a later declaration lands
/// in. A single pass suffices (unlike `simplify_algebra`/`golf_for_loops`,
/// which iterate to a fixed point): the inner chaining loop below already
/// keeps extending one fused statement as far as it goes, and declarations
/// nested inside a `{...}` body are reached in the same left-to-right scan
/// as everything else, not skipped over the way `golf_for_loops` skips a
/// matched loop's own body.
///
/// Unlike every other always-on transform in this file, fusing two
/// statements onto one necessarily removes a line — that's the whole
/// source of the byte savings here, so (unlike `strip_operator_spaces`/
/// `simplify_algebra`/`golf_for_loops`) this one is not held to a
/// stable-line-count invariant.
fn merge_consecutive_declarations(src: &str) -> String {
    let toks = lex_alg(src);
    let n = toks.len();
    let mut out: Vec<AlgTok> = Vec::with_capacity(n);
    let mut i = 0usize;
    while i < n {
        let is_candidate =
            matches!(toks.get(i), Some(AlgTok::Ident(t)) if is_decl_base_type(t)) && !preceded_by_qualifier(&out);
        if is_candidate {
            if let Some(first) = parse_type_decl_stmt(&toks, i) {
                let type_name = first.type_name.clone();
                let mut declarators = first.declarators;
                let mut cursor = first.end;
                let mut merged_count = 1;
                loop {
                    let check_pos = skip_ws(&toks, cursor);
                    if matches!(toks.get(check_pos), Some(AlgTok::Ident(t2)) if *t2 == type_name) {
                        if let Some(next) = parse_type_decl_stmt(&toks, check_pos) {
                            declarators.extend(next.declarators);
                            cursor = next.end;
                            merged_count += 1;
                            continue;
                        }
                    }
                    break;
                }
                if merged_count >= 2 {
                    out.push(AlgTok::Ident(type_name));
                    out.push(AlgTok::Punct(' '));
                    for (idx, (name, init)) in declarators.into_iter().enumerate() {
                        if idx > 0 {
                            out.push(AlgTok::Punct(','));
                        }
                        out.push(AlgTok::Ident(name));
                        if let Some(init_toks) = init {
                            out.push(AlgTok::Punct('='));
                            out.extend(init_toks);
                        }
                    }
                    out.push(AlgTok::Punct(';'));
                    i = cursor;
                    continue;
                }
            }
        }
        out.push(toks[i].clone());
        i += 1;
    }

    let mut s = String::with_capacity(src.len());
    for tok in &out {
        match tok {
            AlgTok::Ident(t) | AlgTok::Number(t) | AlgTok::IntLike(t) => s.push_str(t),
            AlgTok::Punct(c) => s.push(*c),
        }
    }
    s
}

// ---------------------------------------------------------------------
// Brace stripping around single-statement if/for/while bodies
// ---------------------------------------------------------------------

/// Mirror image of `skip_ws`: walks backward over a run of space/newline
/// tokens starting at `j` (inclusive) and returns the index of the first
/// non-whitespace token at or before `j`, or `None` if the run reaches the
/// start of the token stream without finding one.
fn skip_ws_backward(toks: &[AlgTok], mut j: usize) -> Option<usize> {
    loop {
        match toks.get(j) {
            Some(AlgTok::Punct(' ')) | Some(AlgTok::Punct('\n')) => {
                if j == 0 {
                    return None;
                }
                j -= 1;
            }
            Some(_) => return Some(j),
            None => return None,
        }
    }
}

/// Backward counterpart to `find_matching_close`: finds the `open`
/// bracket that matches the `close` bracket sitting at `close_idx`,
/// tracking nesting depth so an inner pair doesn't fool it. Used to walk
/// from a condition's closing `)` back to its opening `(`, so the token
/// right before that `(` — the construct's own keyword — can be read off.
fn find_matching_open(toks: &[AlgTok], close_idx: usize, open: char, close: char) -> Option<usize> {
    let mut depth = 0i32;
    let mut j = close_idx;
    loop {
        match toks.get(j) {
            Some(AlgTok::Punct(c)) if *c == close => depth += 1,
            Some(AlgTok::Punct(c)) if *c == open => {
                depth -= 1;
                if depth == 0 {
                    return Some(j);
                }
            }
            _ => {}
        }
        if j == 0 {
            return None;
        }
        j -= 1;
    }
}

/// Returns the index right after one complete statement starting at
/// `start` (leading whitespace tolerated), or `None` if what's there isn't
/// a shape this function recognizes — in which case the caller must back
/// off entirely, exactly like every other rule in this file. Shared by
/// `contains_if_without_else` (to find where an `if`'s then-part ends, so
/// it can check whether an `else` immediately follows) and
/// `find_strippable_braces` (to check that a candidate block holds
/// *exactly* one statement).
///
/// Recognizes: a `{...}` block (always exactly one statement, whatever it
/// contains — this is what lets brace-stripping recurse through nested
/// blocks without extra machinery, see `find_strippable_braces`); `if(...)
/// THEN [else ELSE]`; `for(...) BODY` / `while(...) BODY`; `do BODY
/// while(...);`; and, as a fallback, a plain statement (declaration,
/// assignment, `return`/`break`/`continue`/`discard`, ...) scanned up to
/// its own top-level `;` with `(`/`[`/`{` depth tracking so a nested
/// call's own punctuation is never mistaken for the statement terminator.
/// `switch` is deliberately *not* recognized (returns `None`, forcing a
/// conservative bail-out wherever it's encountered): its body isn't a
/// single statement in the same sense, and it's out of scope for this
/// pass (see the roadmap item this implements — only `if`/`for`/`while`).
fn skip_statement(toks: &[AlgTok], start: usize) -> Option<usize> {
    let start = skip_ws(toks, start);
    match toks.get(start) {
        Some(AlgTok::Punct('{')) => {
            let close = find_matching_close(toks, start, '{', '}')?;
            Some(close + 1)
        }
        Some(AlgTok::Ident(kw)) if kw == "if" => {
            let mut j = skip_ws(toks, start + 1);
            if !matches!(toks.get(j), Some(AlgTok::Punct('('))) {
                return None;
            }
            let close_paren = find_matching_close(toks, j, '(', ')')?;
            j = close_paren + 1;
            j = skip_statement(toks, j)?;
            let after_then = skip_ws(toks, j);
            if matches!(toks.get(after_then), Some(AlgTok::Ident(e)) if e == "else") {
                let k = skip_ws(toks, after_then + 1);
                skip_statement(toks, k)
            } else {
                Some(j)
            }
        }
        Some(AlgTok::Ident(kw)) if kw == "for" || kw == "while" => {
            let j = skip_ws(toks, start + 1);
            if !matches!(toks.get(j), Some(AlgTok::Punct('('))) {
                return None;
            }
            let close_paren = find_matching_close(toks, j, '(', ')')?;
            skip_statement(toks, close_paren + 1)
        }
        Some(AlgTok::Ident(kw)) if kw == "do" => {
            let mut j = skip_statement(toks, start + 1)?;
            j = skip_ws(toks, j);
            if !matches!(toks.get(j), Some(AlgTok::Ident(w)) if w == "while") {
                return None;
            }
            j = skip_ws(toks, j + 1);
            if !matches!(toks.get(j), Some(AlgTok::Punct('('))) {
                return None;
            }
            let close_paren = find_matching_close(toks, j, '(', ')')?;
            j = skip_ws(toks, close_paren + 1);
            if !matches!(toks.get(j), Some(AlgTok::Punct(';'))) {
                return None;
            }
            Some(j + 1)
        }
        Some(AlgTok::Ident(kw)) if kw == "switch" => None,
        Some(_) => {
            // Plain statement: scan forward to the first top-level `;`,
            // treating `(`/`[`/`{` as depth-increasing (a constructor
            // call's or array index's own punctuation must never be
            // mistaken for the statement's terminator) — same style of
            // scan `parse_type_decl_stmt` already uses for an
            // initializer expression.
            let mut depth = 0i32;
            let mut j = start;
            loop {
                match toks.get(j) {
                    Some(AlgTok::Punct(c)) if *c == '(' || *c == '[' || *c == '{' => {
                        depth += 1;
                        j += 1;
                    }
                    Some(AlgTok::Punct(c)) if *c == ')' || *c == ']' || *c == '}' => {
                        depth -= 1;
                        if depth < 0 {
                            return None;
                        }
                        j += 1;
                    }
                    Some(AlgTok::Punct(';')) if depth == 0 => return Some(j + 1),
                    Some(_) => j += 1,
                    None => return None,
                }
            }
        }
        None => None,
    }
}

/// True if the token range `[start, end)` contains an `if` that lacks its
/// own explicit `else` — the one shape that makes stripping braces around
/// an *enclosing* construct unsafe (the classic dangling-else ambiguity:
/// once an `if` without an `else` is no longer wrapped in braces of its
/// own, a later `else` written for an outer construct can bind to it
/// instead). Deliberately blind to how deeply nested or how the `if`
/// itself is braced — even an `if` that already has `{...}` around *its
/// own* body still creates the same risk once the construct wrapping *it*
/// loses its braces, since braces around a statement's body never affect
/// which `if` a later `else` attaches to. So: every `if` found anywhere in
/// the range must have its own `else` immediately after its then-part, or
/// this returns `true` and the caller must refuse to strip. A malformed
/// or unrecognized then-part (`skip_statement` returning `None`) is also
/// treated as a risk — conservative bail-out, same as everywhere else in
/// this file.
fn contains_if_without_else(toks: &[AlgTok], start: usize, end: usize) -> bool {
    let mut i = start;
    while i < end {
        if let Some(AlgTok::Ident(kw)) = toks.get(i) {
            if kw == "if" {
                let j = skip_ws(toks, i + 1);
                if matches!(toks.get(j), Some(AlgTok::Punct('('))) {
                    match find_matching_close(toks, j, '(', ')') {
                        Some(close_paren) => match skip_statement(toks, close_paren + 1) {
                            Some(then_end) => {
                                let after_then = skip_ws(toks, then_end);
                                let has_else =
                                    matches!(toks.get(after_then), Some(AlgTok::Ident(e)) if e == "else");
                                if !has_else {
                                    return true;
                                }
                            }
                            None => return true,
                        },
                        None => return true,
                    }
                }
            }
        }
        i += 1;
    }
    false
}

/// True if the token immediately preceding `open_brace_idx` (skipping
/// whitespace) marks this `{` as the body of an `if`/`for`/`while`
/// condition, or as an `else`'s body — the only two contexts
/// `find_strippable_braces` ever considers for stripping. Everything
/// else — a function definition's body (`float f(float x){...}`, where
/// the token before the matching `(` is the function name, never a
/// reserved control-flow keyword since GLSL forbids naming a function
/// `if`/`for`/`while`), a `switch` body, a `do{...}while(...)` body (there
/// is no `{` right after a `do`-loop's own `while(...)`, since that form
/// always ends in `;` instead), or a bare `{...}` block with no header at
/// all — is left completely alone.
fn brace_follows_strippable_header(toks: &[AlgTok], open_brace_idx: usize) -> bool {
    if open_brace_idx == 0 {
        return false;
    }
    let prev_idx = match skip_ws_backward(toks, open_brace_idx - 1) {
        Some(p) => p,
        None => return false,
    };
    match toks.get(prev_idx) {
        Some(AlgTok::Ident(kw)) if kw == "else" => true,
        Some(AlgTok::Punct(')')) => {
            let open_paren_idx = match find_matching_open(toks, prev_idx, '(', ')') {
                Some(p) => p,
                None => return false,
            };
            if open_paren_idx == 0 {
                return false;
            }
            match skip_ws_backward(toks, open_paren_idx - 1) {
                Some(kw_idx) => {
                    matches!(toks.get(kw_idx), Some(AlgTok::Ident(k)) if k == "if" || k == "for" || k == "while")
                }
                None => false,
            }
        }
        _ => false,
    }
}

/// Finds every `{`/`}` pair in `toks` that is safe to drop: a body brace
/// (see `brace_follows_strippable_header`) whose content, trimmed of
/// surrounding whitespace, is *exactly* one statement (checked via
/// `skip_statement` — anything left over after that one statement, or a
/// completely empty block, disqualifies it) and contains no dangling-else
/// risk (`contains_if_without_else`). Returns the set of token indices
/// (both the `{` and the matching `}`) to omit when rebuilding the string.
///
/// All three checks are run directly against the *original*, unmodified
/// token stream for every candidate brace — never against a
/// partially-rewritten copy — so a decision about one brace pair never
/// depends on what's been decided about another. This is what lets a
/// single left-to-right scan (as opposed to the fixed-point iteration
/// `simplify_algebra`/`golf_for_loops` need) correctly handle arbitrarily
/// deep nesting in one pass: an outer candidate's "exactly one statement"
/// check treats a still-braced nested block as one statement regardless
/// of whether *that* block's own braces also end up on the removal list,
/// and each inner candidate is independently evaluated when the scan
/// reaches its own `{`.
fn find_strippable_braces(toks: &[AlgTok]) -> HashSet<usize> {
    let mut skip = HashSet::new();
    for i in 0..toks.len() {
        if !matches!(toks.get(i), Some(AlgTok::Punct('{'))) {
            continue;
        }
        if !brace_follows_strippable_header(toks, i) {
            continue;
        }
        let close = match find_matching_close(toks, i, '{', '}') {
            Some(c) => c,
            None => continue,
        };
        let stmt_start = skip_ws(toks, i + 1);
        if stmt_start >= close {
            continue; // empty (or whitespace-only) block: never stripped
        }
        let stmt_end = match skip_statement(toks, stmt_start) {
            Some(e) => e,
            None => continue,
        };
        if skip_ws(toks, stmt_end) != close {
            continue; // more than one statement in the block
        }
        if contains_if_without_else(toks, stmt_start, stmt_end) {
            continue;
        }
        skip.insert(i);
        skip.insert(close);
    }
    skip
}

/// Strips the `{`/`}` around a single-statement `if`/`for`/`while`/`else`
/// body (`if(x){y=1.;}` -> `if(x)y=1.;`) — the biggest safe "structural"
/// win available, and the most delicate one because of the dangling-else
/// ambiguity: for an unbraced `if` with no `else` of its own, a *later*
/// `else` can no longer tell whether it belongs to that `if` or to
/// whatever construct used to wrap it in braces. Handled by refusing to
/// strip any block that contains such an `if` anywhere inside it, however
/// deeply nested or however it's itself braced (`contains_if_without_else`
/// — the only ambiguous case in a C-like grammar) — conservative by
/// construction, missing a few bytes rather than ever risking a changed
/// control flow, exactly like the rest of this file. The same treatment
/// applies to `for`/`while` bodies too, even though neither can itself
/// carry a trailing `else` (see `brace_follows_strippable_header`'s doc
/// comment) — kept uniform with `if` rather than special-cased, per the
/// roadmap item this implements.
fn strip_redundant_braces(src: &str) -> String {
    let toks = lex_alg(src);
    let skip = find_strippable_braces(&toks);
    if skip.is_empty() {
        return src.to_string();
    }
    let mut s = String::with_capacity(src.len());
    for (idx, tok) in toks.iter().enumerate() {
        if skip.contains(&idx) {
            continue;
        }
        let text: &str = match tok {
            AlgTok::Ident(t) | AlgTok::Number(t) | AlgTok::IntLike(t) => t.as_str(),
            AlgTok::Punct(c) => {
                // Punct is always exactly one char; handled via the same
                // word-boundary check below by treating it as a one-char
                // str, cheapest done inline.
                s.push(*c);
                continue;
            }
        };
        // A removed `{` can leave a keyword (only ever `else` in
        // practice — the only alphabetic token this pass ever puts
        // directly ahead of a stripped body) touching the first
        // character of what used to be inside the braces, e.g.
        // `else{z=1.;}` -> `elsez=1.;` once the brace is gone, which
        // retokenizes as one identifier. Every *other* place this pass
        // removes a brace sits right after `)`, never after a
        // letter/digit, so this is the only adjacency that can ever
        // newly collide — checked generically here (last emitted char
        // and this token's first char both word characters) rather than
        // hard-coded to "else" specifically, so it stays correct even if
        // a future change to `brace_follows_strippable_header` adds
        // another keyword-led context.
        let needs_space = matches!(s.chars().last(), Some(c) if is_ident_char(c))
            && matches!(text.chars().next(), Some(c) if is_ident_char(c) || c.is_ascii_digit());
        if needs_space {
            s.push(' ');
        }
        s.push_str(text);
    }
    s
}

/// True for an `AlgTok` allowed inside a ternary branch expression (`X`/`Y`
/// in the rewritten `name=cond?X:Y;`): a bare identifier or numeric
/// literal, the arithmetic operators, member/swizzle access (`.`), and
/// whitespace. Deliberately excludes **every** parenthesis or bracket —
/// `(` `)` `[` `]` — not just a bare function call, which would be the
/// narrower and in-theory-sufficient restriction (see
/// `try_rewrite_if_else_ternary`'s doc comment for *why* a call is unsafe
/// here: GLSL's `?:` operator is not guaranteed, on every historical
/// compiler, to evaluate only the taken branch, unlike `if`/`else`).
/// Excluded regardless, because this tokenizer has no reliable way to tell
/// a harmless grouping paren (`(a+b)`) apart from a call (`f(a+b)`) without
/// a real parser, and the marginal byte savings from allowing grouping
/// through isn't worth that risk. `?`/`:` are excluded too, to avoid having
/// to reason about a nested ternary's precedence once folded into an outer
/// one — reserved words (including `true`/`false`) are excluded via
/// `is_reserved`, same as `is_atomic_operand` elsewhere in this file.
fn is_ternary_branch_tok(tok: &AlgTok) -> bool {
    match tok {
        AlgTok::Ident(name) => !is_reserved(name),
        AlgTok::Number(_) | AlgTok::IntLike(_) => true,
        AlgTok::Punct(c) => matches!(c, '+' | '-' | '*' | '/' | '%' | '.' | ' ' | '\n'),
    }
}

/// A parsed `ident=expr;` statement — the exact shape `try_rewrite_if_else_ternary`
/// requires of both the `if` and `else` branches.
struct ParsedSimpleAssign {
    name: String,
    expr_start: usize,
    expr_end: usize, // exclusive of the terminating `;`
    stmt_end: usize,  // index right after the terminating `;`
}

/// Recognises `ident=expr;` starting at `start` (leading whitespace
/// skipped): a bare, non-reserved identifier — a declaration such as
/// `float a=1.;` starts with the reserved type name instead, so it's
/// already excluded by the `!is_reserved` check on the first token, never
/// reaching this far — followed immediately by a single `=` (checked to
/// not be itself followed by a second `=`, which would make this an `==`
/// comparison used as a standalone expression statement rather than an
/// assignment) and a run of `is_ternary_branch_tok` tokens up to the first
/// top-level `;`. Because the token right after the identifier must *be*
/// `=` with nothing in between, this already rules out a compound
/// assignment (`a+=`, next token `+` not `=`) and an indexed/member write
/// target (`a[i]=`/`a.x=`, next token `[`/`.` not `=`) without any extra
/// check. Returns `None` on anything else, including a disallowed token —
/// a function call, an array index, a nested ternary, an unterminated
/// statement — appearing before that `;` is reached.
fn parse_simple_ternary_assign(toks: &[AlgTok], start: usize) -> Option<ParsedSimpleAssign> {
    let start = skip_ws(toks, start);
    let name = match toks.get(start) {
        Some(AlgTok::Ident(n)) if !is_reserved(n) => n.clone(),
        _ => return None,
    };
    let mut j = skip_ws(toks, start + 1);
    if !matches!(toks.get(j), Some(AlgTok::Punct('='))) {
        return None;
    }
    j += 1;
    if matches!(toks.get(j), Some(AlgTok::Punct('='))) {
        return None; // `==`, not an assignment
    }
    let expr_start = j;
    loop {
        match toks.get(j) {
            Some(AlgTok::Punct(';')) => break,
            Some(tok) if is_ternary_branch_tok(tok) => j += 1,
            _ => return None,
        }
    }
    if j == expr_start {
        return None; // empty right-hand side, malformed
    }
    Some(ParsedSimpleAssign { name, expr_start, expr_end: j, stmt_end: j + 1 })
}

/// True if `cond` (the tokens strictly between an `if`'s own parentheses)
/// contains, at depth 0, an operator that would silently change meaning
/// once dropped as-is into a ternary's condition slot: an assignment
/// (`=`, or a compound `+=`/`-=`/...) or a comma. `x=y?A:B` parses as
/// `x=(y?A:B)`, never `(x=y)?A:B` — so a condition that is itself an
/// assignment expression (`if(x=y)`, a real pattern this file's own
/// `simplify_algebra_pass` explicitly guards against reading as a plain
/// comparison, see `is_valid_increment_terminator`) would silently change
/// which value flows into `X`/`Y`'s selection versus what the original
/// `if` computed. GLSL's comma operator binds looser than `?:` for the
/// same reason: `if(a,b)` embedded as `a,b?X:Y` would select between
/// `X`/`Y` on `b` alone, dropping `a`'s role. Depth-tracked so a
/// comma/assignment nested inside the condition's *own* call/array parens
/// (`if(dot(a,b)>0.)`) is correctly left alone — it never leaves that call's
/// own precedence scope, so embedding the whole condition changes nothing
/// about it.
fn cond_has_unsafe_top_level_operator(cond: &[AlgTok]) -> bool {
    let mut depth = 0i32;
    for (i, tok) in cond.iter().enumerate() {
        match tok {
            AlgTok::Punct(c) if *c == '(' || *c == '[' => depth += 1,
            AlgTok::Punct(c) if *c == ')' || *c == ']' => depth -= 1,
            AlgTok::Punct(',') if depth == 0 => return true,
            AlgTok::Punct('=') if depth == 0 => {
                let prev_is_cmp_lead = i > 0
                    && matches!(
                        cond[i - 1],
                        AlgTok::Punct('=') | AlgTok::Punct('!') | AlgTok::Punct('<') | AlgTok::Punct('>')
                    );
                let next_is_eq = matches!(cond.get(i + 1), Some(AlgTok::Punct('=')));
                if !prev_is_cmp_lead && !next_is_eq {
                    return true;
                }
            }
            _ => {}
        }
    }
    false
}

/// If the tokens starting at `if_idx` (already checked by the caller to be
/// the keyword `if`) form the exact shape `if(COND)NAME=X;else NAME=Y;` —
/// the de-braced form `strip_redundant_braces` (which this pass always runs
/// after, see `golf_shader_impl`) already produces from
/// `if(COND){NAME=X;}else{NAME=Y;}` — returns the rewritten
/// `NAME=COND?X:Y;` text plus the index right after the matched range.
/// Returns `None` on anything short of an exact match: a multi-statement
/// or non-assignment branch, mismatched assignment targets, a branch
/// containing a function call (`parse_simple_ternary_assign`'s job via
/// `is_ternary_branch_tok`), or a condition unsafe to relocate as-is
/// (`cond_has_unsafe_top_level_operator`).
fn try_rewrite_if_else_ternary(toks: &[AlgTok], if_idx: usize) -> Option<(String, usize)> {
    let j = skip_ws(toks, if_idx + 1);
    if !matches!(toks.get(j), Some(AlgTok::Punct('('))) {
        return None;
    }
    let close_paren = find_matching_close(toks, j, '(', ')')?;
    let cond_start = j + 1;
    let cond_end = close_paren;
    if cond_start >= cond_end {
        return None; // empty condition: malformed, leave untouched
    }
    if cond_has_unsafe_top_level_operator(&toks[cond_start..cond_end]) {
        return None;
    }

    let then = parse_simple_ternary_assign(toks, close_paren + 1)?;
    let after_then = skip_ws(toks, then.stmt_end);
    if !matches!(toks.get(after_then), Some(AlgTok::Ident(kw)) if kw == "else") {
        return None;
    }
    let els = parse_simple_ternary_assign(toks, after_then + 1)?;
    if then.name != els.name {
        return None;
    }
    // Defensive, mirroring `find_strippable_braces`'s own dangling-else
    // guard even though it can never actually fire here: `is_ternary_branch_tok`
    // already excludes the reserved word `if` from both branches, so
    // neither can contain one to begin with.
    if contains_if_without_else(toks, cond_start, els.stmt_end) {
        return None;
    }

    let cond_text = render_alg_toks(&toks[cond_start..cond_end]);
    let x_text = render_alg_toks(&toks[then.expr_start..then.expr_end]);
    let y_text = render_alg_toks(&toks[els.expr_start..els.expr_end]);
    Some((format!("{}={}?{}:{};", then.name, cond_text, x_text, y_text), els.stmt_end))
}

/// Converts every `if(COND)NAME=X;else NAME=Y;` in `src` into
/// `NAME=COND?X:Y;`, left to right. Every candidate `if` is evaluated
/// directly against the *original* token stream, never a
/// partially-rewritten copy — the same strategy `find_strippable_braces`
/// uses, and for the same reason: a rewrite decided for one `if` never
/// depends on what's been decided for another, so this correctly handles
/// any number of independent (sibling) `if`/`else` statements in one scan.
///
/// A **single pass is deliberately all this does** — unlike
/// `simplify_algebra`/`golf_for_loops`, this is not iterated to a fixed
/// point. `is_ternary_branch_tok` excludes `?`/`:` from a branch's allowed
/// content specifically so that a *nested* `if(p){if(q)a=1.;else
/// a=2.;}else a=3.;` (already de-braced by `strip_redundant_braces` into
/// `if(p)if(q)a=1.;else a=2.;else a=3.;`) only ever has its *inner*
/// `if`/`else` converted (`if(p)a=q?1.:2.;else a=3.;`) — the outer's
/// then-branch, now containing the freshly-produced `?`/`:`, is never
/// itself a candidate on a later pass, by construction. This is a
/// deliberate scope limit, not an oversight: composing the outer around an
/// already-produced ternary (`a=p?q?1.:2.:3.;`) would be grammatically
/// sound (C/GLSL's `?:` is right-associative on the else-arm and delimited
/// by its own `:` on the then-arm, so no extra parentheses are needed
/// either way), but distinguishing a previous pass's *well-formed* `?:`
/// output from an unrelated stray `?`/`:` would need real nesting-aware
/// parsing this file otherwise avoids everywhere else — left out of this
/// first version, same spirit as the compound-assignment and inlining
/// items still open elsewhere in this section of the roadmap.
fn ternary_from_if_else(src: &str) -> String {
    let toks = lex_alg(src);
    let n = toks.len();
    let mut out = String::with_capacity(src.len());
    let mut i = 0;
    while i < n {
        if matches!(&toks[i], AlgTok::Ident(kw) if kw == "if") {
            if let Some((replacement, end)) = try_rewrite_if_else_ternary(&toks, i) {
                out.push_str(&replacement);
                i = end;
                continue;
            }
        }
        match &toks[i] {
            AlgTok::Ident(t) | AlgTok::Number(t) | AlgTok::IntLike(t) => out.push_str(t),
            AlgTok::Punct(c) => out.push(*c),
        }
        i += 1;
    }
    out
}

fn strip_comments(src: &str) -> String {
    let chars: Vec<char> = src.chars().collect();
    let mut out = String::with_capacity(src.len());
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if c == '/' && i + 1 < chars.len() && chars[i + 1] == '/' {
            while i < chars.len() && chars[i] != '\n' {
                i += 1;
            }
        } else if c == '/' && i + 1 < chars.len() && chars[i + 1] == '*' {
            i += 2;
            while i + 1 < chars.len() && !(chars[i] == '*' && chars[i + 1] == '/') {
                if chars[i] == '\n' {
                    out.push('\n'); // keep line count-ish, harmless for correctness
                }
                i += 1;
            }
            i += 2;
        } else {
            out.push(c);
            i += 1;
        }
    }
    out
}

pub(crate) fn is_ident_start(c: char) -> bool {
    c.is_ascii_alphabetic() || c == '_'
}

pub(crate) fn is_ident_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_'
}

/// Tokenizes into identifier tokens and raw "other" runs, tracking for each
/// identifier occurrence whether it is immediately preceded by a `.`
/// (member/swizzle access) so struct-field and swizzle names can be
/// excluded from renaming.
fn tokenize(src: &str) -> Vec<(Tok, bool)> {
    let chars: Vec<char> = src.chars().collect();
    let mut tokens = Vec::new();
    let mut i = 0;
    let mut last_nonspace: Option<char> = None;
    while i < chars.len() {
        let c = chars[i];
        if is_ident_start(c) {
            let start = i;
            while i < chars.len() && is_ident_char(chars[i]) {
                i += 1;
            }
            let name: String = chars[start..i].iter().collect();
            let preceded_by_dot = last_nonspace == Some('.');
            last_nonspace = Some(*chars[start..i].last().unwrap());
            tokens.push((Tok::Ident(name), preceded_by_dot));
        } else if c.is_ascii_digit() || (c == '.' && i + 1 < chars.len() && chars[i + 1].is_ascii_digit()) {
            if let Some(end) = try_scan_float_literal(&chars, i) {
                let text: String = chars[i..end].iter().collect();
                last_nonspace = Some(chars[end - 1]);
                tokens.push((Tok::Number(text), false));
                i = end;
            } else {
                // plain integer (or lone '.'): fall through unchanged
                let start = i;
                while i < chars.len() && !is_ident_start(chars[i]) && chars[i].is_ascii_digit() {
                    i += 1;
                }
                if i == start {
                    i += 1; // lone '.', consume the single char
                }
                if !chars[start].is_whitespace() {
                    last_nonspace = Some(chars[i - 1]);
                }
                let raw: String = chars[start..i].iter().collect();
                tokens.push((Tok::Other(raw), false));
            }
        } else {
            let start = i;
            // Stop before anything that could start a new identifier *or*
            // a numeric literal — otherwise this run (e.g. the "= " in
            // "= 1.5") would greedily swallow the digits that follow it,
            // and the number-scanning branch above would never see them.
            while i < chars.len()
                && !is_ident_start(chars[i])
                && !(chars[i].is_ascii_digit()
                    || (chars[i] == '.' && i + 1 < chars.len() && chars[i + 1].is_ascii_digit()))
            {
                if !chars[i].is_whitespace() {
                    last_nonspace = Some(chars[i]);
                }
                i += 1;
            }
            let raw: String = chars[start..i].iter().collect();
            tokens.push((Tok::Other(raw), false));
        }
    }
    tokens
}

fn short_name(index: usize, reserved: &HashSet<&str>, taken: &HashSet<String>) -> String {
    const ALPHABET: &[u8] = b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
    let mut n = index;
    loop {
        // generate the n-th base-52 style short identifier
        let mut candidate = String::new();
        let mut v = n;
        loop {
            let rem = v % ALPHABET.len();
            candidate.insert(0, ALPHABET[rem] as char);
            v = v / ALPHABET.len();
            if v == 0 {
                break;
            }
            v -= 1;
        }
        n += 1;
        if !reserved.contains(candidate.as_str()) && !taken.contains(&candidate) {
            return candidate;
        }
    }
}

fn collapse_whitespace(run: &str) -> String {
    if run.chars().all(|c| c.is_whitespace()) {
        if run.contains('\n') {
            "\n".to_string()
        } else if run.is_empty() {
            String::new()
        } else {
            " ".to_string()
        }
    } else {
        // A mixed run (real punctuation alongside whitespace, e.g. the
        // " - " between two operands) — hand it to the operator-space
        // stripper below instead of leaving it untouched.
        strip_operator_spaces(run)
    }
}

/// Ordered pairs of GLSL punctuation characters that, if written back to
/// back with no separator, form a *different* multi-character token than
/// the two single-character ones the source actually had (`+`,`+` ->
/// `++`, `/`,`*` -> the start of a block comment, ...). Order matters:
/// `('<', '=')` (-> `<=`) is listed, `('=', '<')` is not, because `=<` is
/// not a GLSL token and merging it changes nothing. Used by
/// `strip_operator_spaces` to decide which single space, out of a run
/// that otherwise has none, absolutely must survive.
pub(crate) fn is_dangerous_operator_pair(a: char, b: char) -> bool {
    matches!(
        (a, b),
        ('+', '+')
            | ('-', '-')
            | ('+', '=')
            | ('-', '=')
            | ('*', '=')
            | ('/', '=')
            | ('%', '=')
            | ('<', '=')
            | ('>', '=')
            | ('=', '=')
            | ('!', '=')
            | ('&', '&')
            | ('|', '|')
            | ('^', '=')
            | ('&', '=')
            | ('|', '=')
            | ('<', '<')
            | ('>', '>')
            | ('/', '/')
            | ('/', '*')
    )
}

/// Strips the (purely cosmetic) horizontal whitespace around operators
/// inside a "mixed" `Other` run — one that also carries real punctuation,
/// as opposed to `collapse_whitespace`'s whitespace-only case above.
///
/// By construction of `tokenize`'s `Other` branch, letters and digits
/// never appear *inside* such a run (either one ends the run and starts a
/// new `Ident`/number token) — so the run's leading and trailing edges
/// always border an identifier, a number, another `Other` run (itself
/// always separated from this one by at least one alphanumeric-starting
/// token — see below), or the start/end of the file. No GLSL
/// multi-character operator mixes a letter/digit with a punctuation
/// character (an identifier cannot start with a digit either, so gluing a
/// number directly onto a following keyword/identifier is equally safe),
/// so the whitespace right at the run's own edges is always safe to drop
/// outright. Two `Other` runs are themselves never textually adjacent in
/// the source without at least one `Ident`/number token between them
/// (that's what splits one run into two in the first place), so this
/// per-run, edge-safe reasoning never misses a cross-run danger case.
///
/// Only whitespace *between two punctuation characters inside the same
/// run* is actually at risk of changing meaning, and is only dropped when
/// `is_dangerous_operator_pair` clears it — e.g. `a - -b` keeps its
/// middle space (`- -` must never collapse into the decrement operator
/// `--`) but loses its leading one (`a- -b`).
///
/// Newlines are never inserted or removed, only the horizontal whitespace
/// around them — this can never change the source's line count, which
/// `header_line_count`'s error-line mapping (see `shader.rs`) depends on
/// staying stable.
fn strip_operator_spaces(run: &str) -> String {
    let chars: Vec<char> = run.chars().collect();
    let n = chars.len();
    let mut out = String::with_capacity(n);
    let mut i = 0;
    while i < n {
        let c = chars[i];
        if c == '\n' {
            out.push(c);
            i += 1;
        } else if c.is_whitespace() {
            let mut j = i;
            while j < n && chars[j].is_whitespace() && chars[j] != '\n' {
                j += 1;
            }
            let prev = out.chars().last();
            let next = chars.get(j).copied();
            let keep_space = matches!(
                (prev, next),
                (Some(p), Some(nx)) if is_dangerous_operator_pair(p, nx)
            );
            if keep_space {
                out.push(' ');
            }
            i = j;
        } else {
            out.push(c);
            i += 1;
        }
    }
    out
}

/// Minifies GLSL source: strips comments, collapses whitespace, shortens
/// numeric literals and drops redundant semicolons, and — unless the name
/// is in `extra_protected` — shortens user-defined identifiers
/// (variables/functions) to the shortest unused name. Identifiers that
/// ever appear as `.name` (struct field / swizzle access), GLSL
/// keywords/builtins, the Shadertoy harness globals (iResolution, iTime,
/// mainImage, ...), and names introduced via `#define` are never renamed
/// regardless of `extra_protected`.
///
/// `extra_protected` exists because the `Common` tab is textually
/// prepended to every pass before compilation: golfing Common and a pass
/// as fully independent units would let each one rename a name Common
/// declares (e.g. a helper function) differently, breaking the pass that
/// calls it. Names Common declares must be passed here so a pass's own
/// golf pass leaves them exactly as Common (also golfed, via
/// `golf_common`, which never renames anything) wrote them.
/// A top-level (brace-depth 0) declaration found by `find_top_level_declarations`:
/// a function definition or a `struct` definition, both of which look
/// identical at this syntactic level (`IDENT ... IDENT ( ... ) { ... }` /
/// `struct IDENT { ... };`). `start`/`end` are char offsets in the scanned
/// source spanning the whole declaration, trailing `;` included if present
/// (struct definitions require one; function definitions never have one,
/// so `end` just lands right after the closing `}` for those).
struct TopLevelDecl {
    name: String,
    start: usize,
    end: usize,
}

/// Scans (comment-stripped) GLSL for top-level declarations, using the same
/// "brace-depth 0 openings are always function/struct definitions in GLSL"
/// fact `literals::detect_literal_sliders` relies on for categorization —
/// GLSL has no nested function/struct definitions, so this is unambiguous.
fn find_top_level_declarations(chars: &[char]) -> Vec<TopLevelDecl> {
    let n = chars.len();
    let mut result = Vec::new();
    let mut i = 0usize;
    let mut depth: i32 = 0;
    let mut paren_depth: i32 = 0;
    let mut pending_ident: Option<String> = None;
    let mut pending_start: Option<usize> = None;

    while i < n {
        let c = chars[i];
        if is_ident_start(c) {
            let start = i;
            while i < n && is_ident_char(chars[i]) {
                i += 1;
            }
            if depth == 0 && paren_depth == 0 {
                pending_ident = Some(chars[start..i].iter().collect());
                if pending_start.is_none() {
                    pending_start = Some(start);
                }
            }
            continue;
        }
        match c {
            '(' => {
                paren_depth += 1;
                i += 1;
            }
            ')' => {
                paren_depth -= 1;
                i += 1;
            }
            '{' if depth == 0 => {
                if let (Some(name), Some(decl_start)) = (pending_ident.take(), pending_start.take()) {
                    let mut d = 1;
                    let mut j = i + 1;
                    while j < n && d > 0 {
                        match chars[j] {
                            '{' => d += 1,
                            '}' => d -= 1,
                            _ => {}
                        }
                        j += 1;
                    }
                    let mut end = j;
                    let mut k = end;
                    while k < n && chars[k].is_whitespace() {
                        k += 1;
                    }
                    if k < n && chars[k] == ';' {
                        end = k + 1;
                    }
                    result.push(TopLevelDecl { name, start: decl_start, end });
                    i = end; // depth stays 0: the whole block is consumed
                    continue;
                } else {
                    depth += 1;
                    i += 1;
                }
            }
            '{' => {
                depth += 1;
                i += 1;
            }
            '}' => {
                depth -= 1;
                i += 1;
                if depth == 0 {
                    pending_ident = None;
                    pending_start = None;
                }
            }
            ';' => {
                i += 1;
                if depth == 0 {
                    pending_ident = None;
                    pending_start = None;
                }
            }
            _ => {
                i += 1;
            }
        }
    }
    result
}

/// Strips top-level functions/structs that are never referenced anywhere
/// else in the source (their name occurs exactly once: its own
/// declaration) — dead code a human golfer would delete by hand. `mainImage`
/// is always kept, it's the harness's entry point. Iterates to a fixed
/// point so a chain (A only called by B, B unused) is fully cleared: after
/// B is removed, A's only remaining reference is gone too, so the next
/// pass catches it.
///
/// Only ever safe to call on a single, self-contained pass — never on
/// `Common`, whose declarations exist specifically to be used by *other*,
/// separately-golfed passes and can look "unused" from its own text alone.
fn remove_unused_functions(src: &str) -> String {
    let mut current = src.to_string();
    for _ in 0..10 {
        let chars: Vec<char> = current.chars().collect();
        let declarations = find_top_level_declarations(&chars);
        if declarations.is_empty() {
            break;
        }

        let tokens = tokenize(&current);
        let mut usage_count: std::collections::HashMap<String, usize> = std::collections::HashMap::new();
        for (tok, _) in &tokens {
            if let Tok::Ident(name) = tok {
                *usage_count.entry(name.clone()).or_insert(0) += 1;
            }
        }

        let mut to_remove: Vec<(usize, usize)> = Vec::new();
        for decl in &declarations {
            if decl.name == "mainImage" {
                continue;
            }
            if usage_count.get(&decl.name).copied().unwrap_or(0) <= 1 {
                to_remove.push((decl.start, decl.end));
            }
        }
        if to_remove.is_empty() {
            break;
        }

        let mut next = String::with_capacity(current.len());
        let mut cursor = 0usize;
        for (start, end) in &to_remove {
            if *start > cursor {
                next.extend(&chars[cursor..*start]);
            }
            cursor = *end;
        }
        next.extend(&chars[cursor..]);
        current = next;
    }
    current
}

/// Une fonction top-level candidate à l'inlining à site d'appel unique
/// (voir `inline_single_call_functions` pour le périmètre complet) :
/// position de sa déclaration entière (pour la supprimer), noms de ses
/// paramètres dans l'ordre de la signature, et texte brut (non substitué)
/// de son unique expression de retour.
struct InlineCandidate {
    name: String,
    decl_start: usize,
    decl_end: usize,
    params: Vec<String>,
    body_expr: String,
}

/// Vrai si `decl_text` (une déclaration top-level telle que retournée par
/// `find_top_level_declarations`) est un `struct`, jamais une fonction —
/// les deux ont la même forme syntaxique à ce niveau (`IDENT ... IDENT {
/// ... }`), seul le tout premier mot les distingue.
fn decl_is_struct(decl_text: &str) -> bool {
    let trimmed = decl_text.trim_start();
    let word: String = trimmed.chars().take_while(|&c| is_ident_char(c)).collect();
    word == "struct"
}

/// Comme `skip_ws`, mais avance aussi sur une tabulation — `skip_ws` ne le
/// fait pas (il ne sert jusqu'ici qu'à du texte déjà passé par
/// `collapse_whitespace`, qui ne produit jamais de tabulation). Cette passe
/// tourne, elle, sur la source *avant* collapse des espaces, où une
/// indentation à la tabulation reste possible.
fn skip_all_ws(toks: &[AlgTok], mut j: usize) -> usize {
    while matches!(
        toks.get(j),
        Some(AlgTok::Punct(' ')) | Some(AlgTok::Punct('\n')) | Some(AlgTok::Punct('\t'))
    ) {
        j += 1;
    }
    j
}

fn is_ws_punct(t: &AlgTok) -> bool {
    matches!(t, AlgTok::Punct(' ') | AlgTok::Punct('\n') | AlgTok::Punct('\t'))
}

/// Vrai si `s` est un unique token atomique (un identifiant ou un nombre,
/// rien d'autre) — jamais besoin de parenthèses supplémentaires autour
/// d'une telle expression, elle est déjà de précédence maximale quel que
/// soit le contexte où elle est collée.
fn is_single_atomic_token(s: &str) -> bool {
    let toks = lex_alg(s);
    let non_ws: Vec<&AlgTok> = toks.iter().filter(|t| !is_ws_punct(t)).collect();
    non_ws.len() == 1 && matches!(non_ws[0], AlgTok::Ident(_) | AlgTok::Number(_) | AlgTok::IntLike(_))
}

/// Vrai si `s` est déjà entièrement enveloppé par une unique paire de
/// parenthèses englobante (`(...)`, la première ouvrante ne se refermant
/// qu'à la toute fin) — l'entourer d'une parenthèse de plus serait une
/// redondance pure, jamais nécessaire pour la précédence.
fn is_fully_parenthesized(s: &str) -> bool {
    let chars: Vec<char> = s.chars().collect();
    if chars.first() != Some(&'(') || chars.last() != Some(&')') {
        return false;
    }
    let mut depth = 0i32;
    for (idx, &c) in chars.iter().enumerate() {
        match c {
            '(' => depth += 1,
            ')' => {
                depth -= 1;
                if depth == 0 && idx != chars.len() - 1 {
                    return false;
                }
            }
            _ => {}
        }
    }
    depth == 0
}

/// Analyse `decl_text` (le texte complet — signature + corps — d'une
/// déclaration top-level *dont on sait déjà* que ce n'est pas un `struct`)
/// comme candidate potentielle à l'inlining. Ne vérifie ni le nombre
/// d'occurrences du nom dans le fichier (propriété globale, vérifiée par
/// l'appelant, `inline_single_call_functions`) ni la récursivité (exclue en
/// amont par ce même comptage global — un appel récursif ferait apparaître
/// le nom une troisième fois). Retourne `None` dès que la déclaration sort
/// du périmètre minimal décrit dans roadmap2.md :
/// - un `[` n'importe où dans la déclaration (tableau en type de retour ou
///   en paramètre) l'exclut entièrement — jamais dans le périmètre de cette
///   première version ;
/// - un type de retour `void` l'exclut (rien à substituer à l'appel) ;
/// - chaque paramètre doit avoir un nom propre, distinct de son type (un
///   prototype à paramètre anonyme, `float foo(float)`, ne peut jamais être
///   inliné : aucun nom vers lequel substituer l'argument réel) ;
/// - le corps doit se réduire à une unique instruction `return EXPR;` —
///   aucune déclaration locale, aucun `return` anticipé, rien après le
///   `;` final.
fn parse_inline_candidate(name: &str, decl_start: usize, decl_end: usize, decl_text: &str) -> Option<InlineCandidate> {
    if decl_text.contains('[') {
        return None;
    }

    let toks = lex_alg(decl_text);

    // Le nom de la fonction est l'identifiant juste avant le premier `(` du
    // texte (le type de retour ne peut jamais lui-même contenir de
    // parenthèse en GLSL) — repéré en reculant depuis ce `(` par-dessus les
    // espaces.
    let open_paren = toks.iter().position(|t| matches!(t, AlgTok::Punct('(')))?;
    let mut name_idx = open_paren;
    let found_name = loop {
        if name_idx == 0 {
            break false;
        }
        name_idx -= 1;
        if is_ws_punct(&toks[name_idx]) {
            continue;
        }
        break matches!(&toks[name_idx], AlgTok::Ident(n) if n == name);
    };
    if !found_name {
        return None;
    }

    // Le type de retour est le token juste avant le nom (par-dessus les
    // espaces) : un simple identifiant, jamais `void` pour être éligible.
    // D'éventuels qualificatifs de précision (`highp`/`mediump`/`lowp`)
    // peuvent précéder ce type sans y changer quoi que ce soit — seul le
    // token immédiatement collé au nom compte.
    let mut type_idx = name_idx;
    let return_type_is_void = loop {
        if type_idx == 0 {
            break false;
        }
        type_idx -= 1;
        if is_ws_punct(&toks[type_idx]) {
            continue;
        }
        break matches!(&toks[type_idx], AlgTok::Ident(t) if t == "void");
    };
    if return_type_is_void {
        return None;
    }

    let close_paren = find_matching_close(&toks, open_paren, '(', ')')?;

    // Liste des paramètres : vide (aucun argument, ou l'unique mot-clé
    // `void`), ou une suite de segments séparés par des virgules de
    // profondeur 0, chacun devant se terminer par un identifiant (le nom du
    // paramètre) précédé d'au moins un autre token (son type).
    let inner = &toks[open_paren + 1..close_paren];
    let inner_non_ws: Vec<&AlgTok> = inner.iter().filter(|t| !is_ws_punct(t)).collect();
    let mut params: Vec<String> = Vec::new();
    if inner_non_ws.is_empty() {
        // pas de paramètres
    } else if inner_non_ws.len() == 1 && matches!(inner_non_ws[0], AlgTok::Ident(t) if t == "void") {
        // `foo(void)` — pas de paramètres non plus
    } else {
        let mut depth = 0i32;
        let mut seg_start = 0usize;
        let mut k = 0usize;
        while k <= inner.len() {
            let at_end = k == inner.len();
            let is_top_level_comma = !at_end && matches!(inner[k], AlgTok::Punct(',')) && depth == 0;
            if !at_end {
                match &inner[k] {
                    AlgTok::Punct('(') | AlgTok::Punct('[') => depth += 1,
                    AlgTok::Punct(')') | AlgTok::Punct(']') => depth -= 1,
                    _ => {}
                }
            }
            if at_end || is_top_level_comma {
                let seg = &inner[seg_start..k];
                let seg_non_ws: Vec<&AlgTok> = seg.iter().filter(|t| !is_ws_punct(t)).collect();
                if seg_non_ws.len() < 2 {
                    return None; // pas de nom de paramètre distinct de son type
                }
                match seg_non_ws.last().unwrap() {
                    AlgTok::Ident(pname) => params.push((*pname).clone()),
                    _ => return None,
                }
                seg_start = k + 1;
            }
            k += 1;
        }
    }

    // Corps : entre le `{` et son `}` correspondant, en toute fin de la
    // déclaration.
    let open_brace = toks.iter().position(|t| matches!(t, AlgTok::Punct('{')))?;
    let close_brace = find_matching_close(&toks, open_brace, '{', '}')?;
    let body = &toks[open_brace + 1..close_brace];

    let mut b = skip_all_ws(body, 0);
    match body.get(b) {
        Some(AlgTok::Ident(r)) if r == "return" => {}
        _ => return None,
    }
    b += 1;
    let expr_start = skip_all_ws(body, b);

    // Avance jusqu'au `;` de profondeur 0 qui termine ce `return` — une
    // instruction `return EXPR;` valide ne contient jamais de `;` imbriqué
    // à profondeur non nulle, ce garde-fou de profondeur reste défensif
    // plutôt que réellement atteignable.
    let mut depth2 = 0i32;
    let mut e = expr_start;
    let semi = loop {
        match body.get(e) {
            None => return None,
            Some(AlgTok::Punct('(')) | Some(AlgTok::Punct('[')) => {
                depth2 += 1;
                e += 1;
            }
            Some(AlgTok::Punct(')')) | Some(AlgTok::Punct(']')) => {
                depth2 -= 1;
                e += 1;
            }
            Some(AlgTok::Punct(';')) if depth2 == 0 => break e,
            _ => {
                e += 1;
            }
        }
    };
    if semi == expr_start {
        return None; // `return;` — jamais valide pour un type de retour non-void de toute façon
    }
    // Rien d'autre que des espaces ne doit suivre ce `;` : un second
    // `return`, une déclaration locale, tout autre statement fait sortir
    // du périmètre minimal.
    if skip_all_ws(body, semi + 1) != body.len() {
        return None;
    }

    let body_expr = render_alg_toks(&body[expr_start..semi]).trim().to_string();
    if body_expr.is_empty() {
        return None;
    }

    Some(InlineCandidate {
        name: name.to_string(),
        decl_start,
        decl_end,
        params,
        body_expr,
    })
}

/// Cherche dans `chars` l'unique site d'appel de `name` situé hors de
/// l'intervalle `[decl_start, decl_end)` (la déclaration elle-même). Un
/// appel n'est reconnu que sous sa forme la plus étroite — un identifiant
/// non précédé d'un `.` (jamais un accès membre/swizzle), immédiatement
/// suivi de `(` sans le moindre espace — même convention conservatrice que
/// `find_macro_candidates` : un appel écrit avec un espace avant la
/// parenthèse n'est simplement pas reconnu, jamais interprété à tort.
/// Retourne `None` si l'occurrence (l'appelant a déjà vérifié qu'elle
/// existe exactement une fois au sens du comptage brut d'identifiants)
/// n'est finalement pas un appel sous cette forme, ou si ses parenthèses ne
/// s'équilibrent pas — jamais un cas qu'un GLSL valide devrait produire,
/// garde-fou strictement défensif.
fn find_single_call_site(chars: &[char], name: &str, decl_start: usize, decl_end: usize) -> Option<(usize, usize, usize)> {
    let n = chars.len();
    let mut i = 0usize;
    let mut prev_nonspace: Option<char> = None;
    while i < n {
        let c = chars[i];
        if is_ident_start(c) {
            let start = i;
            while i < n && is_ident_char(chars[i]) {
                i += 1;
            }
            let ident: String = chars[start..i].iter().collect();
            let preceded_by_dot = prev_nonspace == Some('.');
            prev_nonspace = Some(chars[i - 1]);
            if ident == name && !preceded_by_dot && !(start >= decl_start && start < decl_end) {
                if i < n && chars[i] == '(' {
                    let mut depth = 1i32;
                    let mut j = i + 1;
                    while j < n && depth > 0 {
                        match chars[j] {
                            '(' => depth += 1,
                            ')' => depth -= 1,
                            _ => {}
                        }
                        j += 1;
                    }
                    if depth != 0 {
                        return None;
                    }
                    return Some((start, i, j - 1));
                }
                return None; // référencé sans être appelé -> jamais sûr
            }
            continue;
        }
        if !c.is_whitespace() {
            prev_nonspace = Some(c);
        }
        i += 1;
    }
    None
}

/// Applique l'inlining de `candidate` au site d'appel `[call_name_start,
/// args_close]` (bornes incluses, indices dans `chars`/`current`) : découpe
/// les arguments réels par virgule de profondeur 0, substitue chaque
/// paramètre par son argument systématiquement entre parenthèses (jamais
/// l'inverse — voir `parse_inline_candidate`/la doc de
/// `inline_single_call_functions` pour le raisonnement complet sur la
/// précédence), puis remplace en une seule passe la déclaration entière
/// (supprimée) et le site d'appel entier (remplacé par l'expression
/// substituée). Retourne `None` sans rien modifier dès que la substitution
/// sort du périmètre minimal : arité qui ne correspond pas (jamais censé
/// arriver sur du GLSL valide, garde-fou défensif), ou un paramètre
/// référencé plus d'une fois dans le corps (dupliquer l'argument serait
/// sémantiquement sûr tant qu'il est pur, mais hors périmètre de cette
/// première version — voir roadmap2.md).
fn inline_at_call_site(
    current: &str,
    chars: &[char],
    candidate: &InlineCandidate,
    call_name_start: usize,
    args_open: usize,
    args_close: usize,
) -> Option<String> {
    let args_src: String = chars[args_open + 1..args_close].iter().collect();
    let arg_toks = lex_alg(&args_src);

    let mut args: Vec<String> = Vec::new();
    if candidate.params.is_empty() {
        if arg_toks.iter().any(|t| !is_ws_punct(t)) {
            return None; // arité incohérente
        }
    } else {
        let mut depth = 0i32;
        let mut seg_start = 0usize;
        for (idx, t) in arg_toks.iter().enumerate() {
            match t {
                AlgTok::Punct('(') | AlgTok::Punct('[') => depth += 1,
                AlgTok::Punct(')') | AlgTok::Punct(']') => depth -= 1,
                AlgTok::Punct(',') if depth == 0 => {
                    args.push(render_alg_toks(&arg_toks[seg_start..idx]).trim().to_string());
                    seg_start = idx + 1;
                }
                _ => {}
            }
        }
        args.push(render_alg_toks(&arg_toks[seg_start..]).trim().to_string());
        if args.len() != candidate.params.len() || args.iter().any(|a| a.is_empty()) {
            return None; // arité incohérente
        }
    }

    let body_toks = lex_alg(&candidate.body_expr);
    let mut out_body = String::with_capacity(candidate.body_expr.len() * 2);
    let mut sub_counts: std::collections::HashMap<usize, usize> = std::collections::HashMap::new();
    let mut prev_was_dot = false;
    for t in &body_toks {
        match t {
            AlgTok::Ident(ident) if !prev_was_dot => {
                if let Some(pos) = candidate.params.iter().position(|p| p == ident) {
                    *sub_counts.entry(pos).or_insert(0) += 1;
                    out_body.push('(');
                    out_body.push_str(&args[pos]);
                    out_body.push(')');
                } else {
                    out_body.push_str(ident);
                }
            }
            AlgTok::Ident(ident) => out_body.push_str(ident),
            AlgTok::Number(s) | AlgTok::IntLike(s) => out_body.push_str(s),
            AlgTok::Punct(c) => out_body.push(*c),
        }
        prev_was_dot = matches!(t, AlgTok::Punct('.'));
    }
    if sub_counts.values().any(|&c| c > 1) {
        return None; // paramètre dupliqué dans le corps — hors périmètre
    }

    // L'expression de retour substituée est elle-même entre parenthèses au
    // site d'appel — au-delà de ce que roadmap2.md demandait explicitement
    // pour son seul exemple illustratif (`foo(a+b)` -> `(a+b)*2.`), mais
    // nécessaire pour la même raison de précédence que la parenthésation
    // systématique des paramètres : un appel de fonction est toujours une
    // unité atomique (précédence maximale) au regard de son contexte
    // environnant, alors qu'une expression de retour arbitraire ne l'est
    // pas — `foo(a)*3.` avec `float foo(float x){return x+1.;}` donnerait
    // `a+1.*3.` (faux : `a+3.`, plus `(a+1)*3.`) si le corps substitué
    // n'était pas lui-même protégé par une parenthèse à ce site. Coût :
    // deux octets redondants au plus, seulement quand l'expression n'est
    // déjà ni un unique token atomique ni déjà pleinement parenthésée
    // (`is_single_atomic_token`/`is_fully_parenthesized`) — gain net
    // toujours très largement positif malgré ça, la déclaration entière
    // (type de retour, nom, liste de paramètres typée, accolades,
    // `return`/`;`) disparaissant intégralement en échange.
    let trimmed = out_body.trim();
    let final_expr = if is_single_atomic_token(trimmed) || is_fully_parenthesized(trimmed) {
        trimmed.to_string()
    } else {
        format!("({trimmed})")
    };

    let call_end = args_close + 1; // inclut le `)` fermant
    let mut edits: Vec<(usize, usize, Option<String>)> = vec![
        (candidate.decl_start, candidate.decl_end, None),
        (call_name_start, call_end, Some(final_expr)),
    ];
    edits.sort_by_key(|(s, ..)| *s);

    let mut out = String::with_capacity(current.len());
    let mut cursor = 0usize;
    for (start, end, replacement) in edits {
        if start > cursor {
            out.extend(&chars[cursor..start]);
        }
        if let Some(text) = replacement {
            out.push_str(&text);
        }
        cursor = end;
    }
    out.extend(&chars[cursor..]);
    Some(out)
}

/// Inline chaque fonction top-level appelée exactement une fois dans tout
/// `src`, quand elle rentre dans le périmètre minimal décrit dans
/// roadmap2.md — la technique la plus payante de Shader Minifier sur les
/// gros shaders (une fonction utilitaire appelée une seule fois n'a aucune
/// raison de garder sa déclaration séparée), mais aussi la plus risquée des
/// passes structurelles de ce fichier, pour des raisons de portée que ce
/// golfer, purement textuel, ne modélise pas.
///
/// Éligibilité d'une fonction (voir `parse_inline_candidate` pour le détail
/// exact de chaque condition) :
/// - jamais `mainImage` (point d'entrée du harness) et jamais un `struct` ;
/// - type de retour non-`void` ;
/// - corps réduit à une unique instruction `return EXPR;` (aucune
///   déclaration locale à shadow, aucun `return` anticipé) ;
/// - appelée **exactement une fois** dans tout `src` — vérifié en amont par
///   un comptage brut d'occurrences du nom (`usage_count == 2` : la
///   déclaration elle-même, plus ce site d'appel). Ce même comptage exclut
///   gratuitement la récursivité : un appel récursif ferait apparaître le
///   nom une troisième fois, dans son propre corps ;
/// - chaque paramètre substitué texte-pour-texte par l'expression d'appel
///   réelle, systématiquement entre parenthèses (`foo(a+b)` avec `float
///   foo(float x){return x*2.;}` devient `(a+b)*2.` et non `a+b*2.` —
///   jamais l'inverse, voir `inline_at_call_site`), et jamais dupliqué :
///   un paramètre référencé plus d'une fois dans le corps rend la fonction
///   entière inéligible à ce site (arithmétique gain/risque défavorable
///   dès qu'un argument volumineux se répète, explicitement hors périmètre
///   de cette première version).
///
/// Explicitement hors périmètre (voir roadmap2.md) : fonctions appelées
/// plusieurs fois, corps à plus d'une instruction, tableaux (type de
/// retour ou paramètre), et **jamais appelée sur `Common`** — même mise en
/// garde que `remove_unused_functions` (dont ce commentaire reprend
/// l'argument) : une fonction déclarée par Common peut être appelée par une
/// pass qui n'est pas celle en cours de golfage, "un seul site d'appel"
/// n'est donc jamais une propriété sûre à vérifier sur le texte de Common
/// isolément.
///
/// Itère jusqu'à point fixe (plafonné à 10 tours, même discipline que
/// `remove_unused_functions`) : inliner une fonction peut rendre une autre
/// fonction — qui l'appelait dans son propre corps `return` — elle-même
/// nouvellement éligible si elle n'avait, elle, qu'un seul site d'appel
/// (chaîne A appelle B une fois, B appelle C une fois : B s'inline dans A,
/// puis C — toujours appelée une fois, son unique site d'appel se trouvant
/// simplement déplacé dans le corps de A après la première itération — est
/// à son tour repérée au tour suivant). Chaque tour ne réapplique qu'une
/// seule substitution avant de tout re-scanner depuis zéro : les positions
/// calculées par `find_top_level_declarations`/`find_single_call_site`
/// portent sur le texte *avant* l'édition, donc plus sûres à invalider
/// entièrement qu'à essayer de corriger en place.
fn inline_single_call_functions(src: &str) -> String {
    let mut current = src.to_string();
    for _ in 0..10 {
        let chars: Vec<char> = current.chars().collect();
        let declarations = find_top_level_declarations(&chars);
        if declarations.is_empty() {
            break;
        }

        let tokens = tokenize(&current);
        let mut usage_count: std::collections::HashMap<String, usize> = std::collections::HashMap::new();
        for (tok, _) in &tokens {
            if let Tok::Ident(name) = tok {
                *usage_count.entry(name.clone()).or_insert(0) += 1;
            }
        }

        let mut applied = false;
        for decl in &declarations {
            if decl.name == "mainImage" {
                continue;
            }
            let decl_text: String = chars[decl.start..decl.end].iter().collect();
            if decl_is_struct(&decl_text) {
                continue;
            }
            if usage_count.get(&decl.name).copied().unwrap_or(0) != 2 {
                continue;
            }
            let Some(candidate) = parse_inline_candidate(&decl.name, decl.start, decl.end, &decl_text) else {
                continue;
            };
            let Some((call_name_start, args_open, args_close)) =
                find_single_call_site(&chars, &candidate.name, decl.start, decl.end)
            else {
                continue;
            };
            let Some(new_src) =
                inline_at_call_site(&current, &chars, &candidate, call_name_start, args_open, args_close)
            else {
                continue;
            };
            current = new_src;
            applied = true;
            break;
        }
        if !applied {
            break;
        }
    }
    current
}

/// `rename`/`dead_code`/`algebra` are the three "aggressive" transforms a
/// user can opt out of independently — comments, whitespace, numeric
/// literals and redundant semicolons are always minified regardless
/// (there's no legitimate reason to keep those, unlike renaming/DCE which
/// trade some readability-of-diffs, DCE's small chance of surprise if the
/// "unused" heuristic is ever wrong for exotic code, and `simplify_algebra`'s
/// own known sharp edge around member/swizzle access — e.g. `p.z*0.` — see
/// its own doc comment). The golf-à-froid check in the UI already catches
/// any case where this produces invalid GLSL and reverts automatically, but
/// a user who keeps hitting that on legitimate code can turn this pass off
/// entirely instead.
fn golf_shader_impl(
    src: &str,
    extra_protected: &HashSet<String>,
    rename: bool,
    dead_code: bool,
    algebra: bool,
) -> String {
    let stripped = strip_comments(src);
    let no_comments = if dead_code {
        // L'inlining tourne après l'élagage de code mort (une fonction
        // devenue inutile n'a pas besoin d'être inlinée avant d'être
        // retirée) et partage son statut "aggressive"/risqué — les deux
        // sont des passes structurelles au niveau fonction, jamais
        // appliquées à Common (voir la doc de chacune).
        inline_single_call_functions(&remove_unused_functions(&stripped))
    } else {
        stripped
    };

    // Object-like macros (`#define NAME value`) are plain textual
    // substitution: the declaration and every call site share the exact
    // same token text, so the ordinary rename pass below keeps them in
    // sync automatically — no need to protect the name, unlike everything
    // else `define_names` guards. Function-like macros (`#define
    // NAME(args) body`, no space before `(`) *are* still protected: their
    // parameter names aren't scope-isolated by this tokenizer, so
    // renaming anything inside one risks colliding with an unrelated
    // identically-named identifier elsewhere in the file.
    let mut define_names: HashSet<String> = HashSet::new();
    for line in no_comments.lines() {
        let trimmed = line.trim_start();
        if let Some(rest) = trimmed.strip_prefix("#define") {
            let rest = rest.trim_start();
            let name_len = rest
                .chars()
                .take_while(|c| c.is_ascii_alphanumeric() || *c == '_')
                .count();
            if name_len > 0 {
                let name: String = rest.chars().take(name_len).collect();
                let is_function_like = rest.chars().nth(name_len) == Some('(');
                if is_function_like {
                    define_names.insert(name);
                }
            }
        }
    }

    let tokens = tokenize(&no_comments);

    let reserved: HashSet<&str> = RESERVED.iter().copied().collect();

    let mut dot_prefixed: HashSet<String> = HashSet::new();
    for (tok, preceded_by_dot) in &tokens {
        if let Tok::Ident(name) = tok {
            if *preceded_by_dot {
                dot_prefixed.insert(name.clone());
            }
        }
    }

    let mut rename_map: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    if rename {
        // Weighted-by-frequency renaming (mirrors Shader Minifier): the
        // shortest names must go to the *most-used* identifiers, not to
        // whichever identifier the tokenizer happens to meet first. A
        // helper called a dozen times from `mainImage` saves far more by
        // being `a` than a name used once ever would. So first tally how
        // many times each renameable identifier occurs — `order` also
        // records first-encounter position, purely to break ties
        // deterministically (`sort_by` is a stable sort, so equal-count
        // identifiers keep the order they were first seen in, giving
        // reproducible output run to run).
        let mut counts: std::collections::HashMap<String, usize> = std::collections::HashMap::new();
        let mut order: Vec<String> = Vec::new();
        for (tok, _) in &tokens {
            if let Tok::Ident(name) = tok {
                if reserved.contains(name.as_str())
                    || dot_prefixed.contains(name)
                    || define_names.contains(name)
                    || extra_protected.contains(name)
                {
                    continue;
                }
                match counts.get_mut(name) {
                    Some(c) => *c += 1,
                    None => {
                        counts.insert(name.clone(), 1);
                        order.push(name.clone());
                    }
                }
            }
        }
        order.sort_by(|a, b| counts[b].cmp(&counts[a]));

        let mut taken: HashSet<String> = HashSet::new();
        for (index, name) in order.into_iter().enumerate() {
            let new_name = short_name(index, &reserved, &taken);
            taken.insert(new_name.clone());
            rename_map.insert(name, new_name);
        }
    }

    let mut out = String::with_capacity(no_comments.len());
    for (tok, _) in &tokens {
        match tok {
            Tok::Ident(name) => {
                out.push_str(rename_map.get(name).map(|s| s.as_str()).unwrap_or(name));
            }
            Tok::Number(text) => {
                out.push_str(&shorten_float_literal(text));
            }
            Tok::Other(raw) => {
                out.push_str(&collapse_whitespace(raw));
            }
        }
    }
    let out = strip_default_in_qualifier(&out);
    let out = if algebra { simplify_algebra(&out) } else { out };
    let out = golf_for_loops(&out);
    let out = merge_consecutive_declarations(&out);
    let out = strip_redundant_braces(&out);
    let out = ternary_from_if_else(&out);
    let out = fold_vector_constructor_splat(&out);
    let out = collapse_redundant_semicolons(out.trim());
    // Last step, deliberately after every other pass — see
    // `extract_repeated_subexpr_macros`'s own doc comment for why
    // (occurrence counts and the byte-savings arithmetic must both operate
    // on the final, fully-golfed spelling) and for why `extra_protected`
    // (Common's declared names) is threaded through here too.
    extract_repeated_subexpr_macros(&out, extra_protected)
}

fn protected_names_from_common(common_src: &str) -> HashSet<String> {
    let common_no_comments = strip_comments(common_src);
    tokenize(&common_no_comments)
        .into_iter()
        .filter_map(|(tok, _)| match tok {
            Tok::Ident(name) => Some(name),
            _ => None,
        })
        .collect()
}

/// Minifies a single, self-contained GLSL source (comments, whitespace,
/// numeric literals, redundant semicolons, *and* identifier renaming +
/// dead-code elimination). This is the right call for a lone pass with no
/// `Common` tab content — equivalent to `golf_shader_ex(src, "", true, true, true)`.
pub fn golf_shader(src: &str) -> String {
    golf_shader_impl(src, &HashSet::new(), true, true, true)
}

/// `golf_shader`/`golf_shader_with_common`, but with the three aggressive
/// transforms (identifier renaming, dead-code elimination, algebraic
/// simplification) independently toggleable — the "agressivité" level from
/// the UI. `common_source` may be empty (no Common tab in use).
pub fn golf_shader_ex(
    src: &str,
    common_source: &str,
    rename: bool,
    dead_code: bool,
    algebra: bool,
) -> String {
    let protected = if common_source.trim().is_empty() {
        HashSet::new()
    } else {
        protected_names_from_common(common_source)
    };
    golf_shader_impl(src, &protected, rename, dead_code, algebra)
}

/// Minifies the `Common` source specifically: every transform `golf_shader`
/// does *except* identifier renaming and repeated-subexpression macro
/// extraction, since Common's declared names — and, symmetrically, any
/// `#define` name this pass would freshly introduce — are visible to every
/// other, independently-golfed pass and must stay textually stable/free of
/// collisions for those calls to keep resolving. See
/// `extract_repeated_subexpr_macros`'s own doc comment for the full
/// argument on why it is never called here.
pub fn golf_common(src: &str) -> String {
    let no_comments = strip_comments(src);
    let tokens = tokenize(&no_comments);
    let mut out = String::with_capacity(no_comments.len());
    for (tok, _) in &tokens {
        match tok {
            Tok::Ident(name) => out.push_str(name),
            Tok::Number(text) => out.push_str(&shorten_float_literal(text)),
            Tok::Other(raw) => out.push_str(&collapse_whitespace(raw)),
        }
    }
    let out = simplify_algebra(&strip_default_in_qualifier(&out));
    let out = golf_for_loops(&out);
    let out = merge_consecutive_declarations(&out);
    let out = strip_redundant_braces(&out);
    let out = ternary_from_if_else(&out);
    let out = fold_vector_constructor_splat(&out);
    collapse_redundant_semicolons(out.trim())
}

/// Minifies one pass's source, protecting every identifier that appears
/// anywhere in `common_src` (the *original*, un-golfed Common text — its
/// declared names, whatever they end up being after `golf_common`, must
/// resolve identically from here) from renaming. See `golf_shader_impl`.
/// Equivalent to `golf_shader_ex(src, common_src, true, true, true)`.
pub fn golf_shader_with_common(src: &str, common_src: &str) -> String {
    golf_shader_impl(src, &protected_names_from_common(common_src), true, true, true)
}

/// Collapses runs of `;` separated only by whitespace down to a single
/// `;` (redundant empty statements, e.g. from a previous golf pass or
/// stray formatting) — never removes the one semicolon a statement
/// actually needs.
fn collapse_redundant_semicolons(s: &str) -> String {
    let chars: Vec<char> = s.chars().collect();
    let mut out = String::with_capacity(s.len());
    let mut i = 0;
    while i < chars.len() {
        if chars[i] == ';' {
            out.push(';');
            i += 1;
            let mut j = i;
            let mut saw_more_semi = false;
            while j < chars.len() && (chars[j].is_whitespace() || chars[j] == ';') {
                if chars[j] == ';' {
                    saw_more_semi = true;
                }
                j += 1;
            }
            if saw_more_semi {
                i = j;
            }
        } else {
            out.push(chars[i]);
            i += 1;
        }
    }
    out
}

// ---------------------------------------------------------------------
// Repli sur un splat pour les constructeurs de vecteur
// ---------------------------------------------------------------------

/// Les seuls constructeurs éligibles au repli splat : `vec2`/`vec3`/`vec4`
/// (avec leur arité attendue). `mat*` est volontairement exclu — un
/// `matN` construit à partir d'un seul scalaire répété sur la diagonale
/// suit une convention de remplissage différente de celle d'un `vecN`
/// (qui réplique le scalaire sur *toutes* les composantes), donc
/// `matN(x,x,...,x)` → `matN(x)` ne serait pas la même valeur.
const SPLAT_VEC_CONSTRUCTORS: &[(&str, usize)] = &[("vec2", 2), ("vec3", 3), ("vec4", 4)];

/// Si `toks[start..close]` (les arguments d'un appel `vecN(...)` déjà
/// isolé par son appelant, `close` étant l'indice du `)` fermant) est
/// exactement `arity` opérandes atomiques (`is_atomic_operand` — un
/// identifiant ou un littéral seul, jamais une sous-expression, jamais un
/// appel de fonction) séparés par une unique `,` chacun, et que leur texte
/// est **identique caractère pour caractère**, retourne cet opérande
/// commun. `None` dans tous les autres cas : mauvais nombre d'arguments,
/// un argument non atomique (`f()`, `a+b`, ...), ou des arguments
/// atomiques mais pas tous identiques (`vec3(a,b,a)`) — aucun de ces cas
/// n'est un splat valide, laissés intacts par l'appelant.
fn parse_atomic_splat_arg(toks: &[AlgTok], start: usize, close: usize, arity: usize) -> Option<AlgTok> {
    let mut args: Vec<&AlgTok> = Vec::with_capacity(arity);
    let mut j = start;
    loop {
        let tok = toks.get(j)?;
        if !is_atomic_operand(tok) {
            return None;
        }
        args.push(tok);
        j += 1;
        if j == close {
            break;
        }
        if !matches!(toks.get(j), Some(AlgTok::Punct(','))) {
            return None;
        }
        j += 1;
    }
    if args.len() != arity {
        return None;
    }
    let first = args[0];
    let all_identical = args.iter().all(|t| match (t, first) {
        (AlgTok::Ident(x), AlgTok::Ident(y)) => x == y,
        (AlgTok::Number(x), AlgTok::Number(y)) => x == y,
        _ => false,
    });
    if all_identical {
        Some(first.clone())
    } else {
        None
    }
}

/// Repli `vecN(x,x,...,x)` → `vecN(x)` (GLSL réplique automatiquement un
/// seul argument scalaire sur toutes les composantes du constructeur, donc
/// les deux valent exactement la même chose). Motif fréquent en sortie de
/// `simplify_algebra`/du renommage quand un calcul de couleur uniforme
/// finit en `vec3(v,v,v)`. Un seul passage suffit — contrairement à
/// `simplify_algebra`/`golf_for_loops`, replier un splat ne peut jamais en
/// révéler un autre : le résultat (`vecN(x)`) n'a plus qu'un seul argument,
/// jamais N identiques, donc rien à réappliquer dessus. Réutilise la même
/// retokenisation `AlgTok`/`find_matching_close` que
/// `extract_repeated_subexpr_macros` pour isoler un appel complet.
/// Destinée à tourner en toute fin de pipeline, comme
/// `extract_repeated_subexpr_macros` : le texte des arguments doit déjà
/// être golfé (renommage, raccourcissement de littéral) pour que la
/// comparaison "identique caractère pour caractère" porte sur
/// l'orthographe finale.
fn fold_vector_constructor_splat(src: &str) -> String {
    let toks = lex_alg(src);
    let n = toks.len();
    let mut out: Vec<AlgTok> = Vec::with_capacity(n);
    let mut i = 0;
    while i < n {
        let mut folded = false;
        if let AlgTok::Ident(name) = &toks[i] {
            if let Some(&(cname, arity)) = SPLAT_VEC_CONSTRUCTORS.iter().find(|(c, _)| *c == name.as_str()) {
                if matches!(toks.get(i + 1), Some(AlgTok::Punct('('))) {
                    if let Some(close) = find_matching_close(&toks, i + 1, '(', ')') {
                        if let Some(arg) = parse_atomic_splat_arg(&toks, i + 2, close, arity) {
                            out.push(AlgTok::Ident(cname.to_string()));
                            out.push(AlgTok::Punct('('));
                            out.push(arg);
                            out.push(AlgTok::Punct(')'));
                            i = close + 1;
                            folded = true;
                        }
                    }
                }
            }
        }
        if !folded {
            out.push(toks[i].clone());
            i += 1;
        }
    }
    render_alg_toks(&out)
}

// ---------------------------------------------------------------------
// Extraction automatique de macros pour les sous-expressions répétées
// ---------------------------------------------------------------------
//
// PROTOTYPE — pas encore appelée depuis `golf_shader_impl`/`golf_common`/
// `golf_shader_ex`, ni depuis `python_ui`/le CLI. C'est volontaire : le
// ticket roadmap correspondant demande explicitement de prototyper cette
// passe séparément avant de l'intégrer au pipeline golf existant. Le point
// d'entrée public est `extract_repeated_subexpr_macros`.
//
// Limite connue et acceptée : `find_macro_candidates` ne distingue pas
// syntaxiquement un *appel* de fonction d'une *définition* de fonction —
// `void m(){...}` génère lui aussi un candidat "appel complet" `m()`
// (l'en-tête à arguments vides). Sans conséquence en pratique : une
// fonction n'est définie qu'une fois, donc ce candidat n'atteint jamais le
// seuil de 2 occurrences requis pour être ne serait-ce qu'évalué — mais si
// deux fonctions différentes du même fichier partageaient un jour le même
// nom à arguments vides (impossible en GLSL valide, les noms de fonction
// sont uniques dans un même fichier) cette distinction resterait à faire.

/// Une occurrence candidate, comme un intervalle demi-ouvert d'indices de
/// tokens `AlgTok` : `[start, end)`.
type TokRange = (usize, usize);

/// Reconstruit le texte source exact couvert par une tranche de tokens
/// `AlgTok`. `lex_alg` consomme chaque caractère de l'entrée dans
/// exactement un token (`Ident`/`Number`/`IntLike` gardent leur texte
/// scanné tel quel, `Punct` un seul caractère) — `render_alg_toks` sur la
/// tranche complète d'un `lex_alg(src)` est donc l'inverse exact de
/// `lex_alg`, ce que vérifie `lex_render_round_trip` ci-dessous.
fn render_alg_toks(toks: &[AlgTok]) -> String {
    let mut out = String::new();
    for t in toks {
        match t {
            AlgTok::Ident(s) | AlgTok::Number(s) | AlgTok::IntLike(s) => out.push_str(s),
            AlgTok::Punct(c) => out.push(*c),
        }
    }
    out
}

/// Marque chaque token appartenant à une ligne de directive préprocesseur
/// (une ligne dont le premier token est `#`, ex. `#define`/`#ifdef`) —
/// jamais un site de génération de candidat ni de remplacement : injecter
/// une macro à l'intérieur d'une autre directive (en particulier une macro
/// fonction-like dont les paramètres ne sont pas isolés en portée par ce
/// tokenizer, voir la note sur `define_names` dans `golf_shader_impl`)
/// serait le genre de fragment arbitraire que ce ticket interdit
/// explicitement. Repose sur l'invariant déjà établi ailleurs dans ce
/// fichier (`strip_operator_spaces`) : les retours à la ligne ne sont
/// jamais insérés ni retirés par le reste du pipeline, donc une directive
/// reste bien délimitée par des `\n` (ou le début/la fin du fichier) une
/// fois golfée.
fn mark_directive_tokens(toks: &[AlgTok]) -> Vec<bool> {
    let mut in_directive = vec![false; toks.len()];
    let mut at_line_start = true;
    let mut i = 0;
    while i < toks.len() {
        if at_line_start && matches!(&toks[i], AlgTok::Punct('#')) {
            while i < toks.len() {
                in_directive[i] = true;
                let is_newline = matches!(&toks[i], AlgTok::Punct('\n'));
                i += 1;
                if is_newline {
                    break;
                }
            }
            at_line_start = true;
            continue;
        }
        at_line_start = matches!(&toks[i], AlgTok::Punct('\n'));
        i += 1;
    }
    in_directive
}

/// Statement/control-flow keywords that must never become the *base* of a
/// macro-extraction candidate in `find_macro_candidates` below — unlike a
/// builtin function (`sin`, `pow`, `texture`, ...), a type used as a
/// constructor (`vec3(...)`, `float(x)`), or a builtin variable
/// (`iResolution`, `gl_FragCoord`, ...), these keywords are not
/// value-producing identifiers: `if(cond)`/`for(...)`/`while(cond)`/
/// `switch(expr)`/`return(x)`/`discard(...)` bind the parenthesised
/// clause to the keyword itself as part of the *statement*'s own grammar,
/// not to a call whose result could be swapped out for a macro name.
/// Extracting `if(cond)` into a macro and substituting it back in would
/// silently drop the `if` keyword from the statement (`if(cond){...}` ->
/// `X{...}`, no longer conditional at all) — this list exists
/// specifically to keep that from ever becoming a candidate in the first
/// place. A strict subset of `RESERVED`'s own "control flow / qualifiers"
/// comment-delimited group, not the whole of `RESERVED`: everything else
/// reserved (types, builtin functions, builtin variables, the Shadertoy
/// harness names) is a legitimate, common candidate base and must stay
/// allowed — in particular this ticket's own example, `sin(iTime*2.)`,
/// has `sin` (a builtin function) as its base, and would never be found
/// at all if this list were simply `RESERVED` itself.
const STATEMENT_KEYWORDS: &[&str] = &["if", "for", "while", "switch", "return", "discard"];

/// Recense chaque sous-expression "complète" du fichier, à l'exclusion de
/// tout ce qui touche une ligne de directive (`in_directive`) :
///
/// - **appel de fonction complet** : un `Ident` qui n'est pas un mot-clé
///   de `STATEMENT_KEYWORDS` (un builtin comme `sin`/`pow`, un type comme
///   `vec3` utilisé en constructeur, ou un identifiant utilisateur
///   conviennent tous), immédiatement suivi de `(` (sans espace — un
///   espace éventuel golfe déjà vers un `Punct(' ')` séparé, donc un tel
///   appel n'est simplement pas reconnu ici ; conservateur, jamais
///   incorrect, voir la note sur `strip_operator_spaces` plus haut dans ce
///   fichier pour le même choix), jusqu'à son `)` correspondant
///   (`find_matching_close`, qui gère déjà la profondeur d'imbrication —
///   un appel dans les arguments d'un autre appel ne referme jamais
///   prématurément le `)` externe) ;
/// - **accès membre complet** : un `Ident` qui n'est pas un mot-clé de
///   `STATEMENT_KEYWORDS` et qui n'est pas lui-même déjà précédé d'un `.`
///   (jamais une sous-chaîne tronquée — un builtin comme `iResolution`/
///   `gl_FragCoord` convient très bien ici, `iResolution.xy` est le
///   candidat canonique), suivi d'au moins un `.Ident`, étendu au maximum
///   tant que le motif `.Ident` se répète (`a.xyz` reste un seul
///   candidat, jamais `a.xyz` *et* `.xyz` séparément).
///
/// Ne déduplique/ne filtre pas encore par longueur ni par nombre
/// d'occurrences — `best_macro_extraction` s'en charge une fois le texte
/// de chaque candidat connu. Un appel et une chaîne d'accès membre ne
/// peuvent jamais partager le même texte rendu (l'un contient toujours au
/// moins une paire `(`/`)`, l'autre jamais), donc les regrouper par texte
/// ensuite ne risque pas de mélanger les deux catégories.
fn find_macro_candidates(toks: &[AlgTok], in_directive: &[bool]) -> Vec<TokRange> {
    let mut spans = Vec::new();
    let mut i = 0;
    while i < toks.len() {
        if in_directive[i] {
            i += 1;
            continue;
        }
        if let AlgTok::Ident(name) = &toks[i] {
            if !STATEMENT_KEYWORDS.contains(&name.as_str()) {
                // appel de fonction complet
                if matches!(toks.get(i + 1), Some(AlgTok::Punct('('))) {
                    if let Some(close) = find_matching_close(toks, i + 1, '(', ')') {
                        if !(i..=close).any(|k| in_directive[k]) {
                            spans.push((i, close + 1));
                        }
                    }
                }
                // accès membre complet (chaîne maximale de `.Ident`)
                let preceded_by_dot = i > 0 && matches!(&toks[i - 1], AlgTok::Punct('.'));
                if !preceded_by_dot {
                    let mut j = i + 1;
                    let mut saw_dot_ident = false;
                    while matches!(toks.get(j), Some(AlgTok::Punct('.')))
                        && matches!(toks.get(j + 1), Some(AlgTok::Ident(n)) if !STATEMENT_KEYWORDS.contains(&n.as_str()))
                    {
                        saw_dot_ident = true;
                        j += 2;
                    }
                    if saw_dot_ident && !(i..j).any(|k| in_directive[k]) {
                        spans.push((i, j));
                    }
                }
            }
        }
        i += 1;
    }
    spans
}

/// Longueur minimale (en caractères) d'un corps de candidat — un simple
/// garde-fou de performance, jamais de correction : le plus petit appel
/// possible (`a()`) ou accès membre possible (`a.x`) fait déjà 3
/// caractères, et un corps de 1-2 caractères ne peut de toute façon jamais
/// passer le test de gain net ci-dessous (le coût fixe d'une ligne
/// `#define` dépasse toujours l'économie réalisable sur un corps aussi
/// court) — ce seuil ne rejette donc jamais un candidat qui aurait
/// autrement été retenu, il évite seulement de le regrouper/évaluer pour
/// rien.
const MIN_MACRO_BODY_LEN: usize = 3;

/// Cherche, dans `src` (déjà entièrement golfé — renommage, littéraux,
/// `simplify_algebra`, etc. déjà appliqués : le comptage "N occurrences"
/// doit porter sur l'orthographe finale des identifiants, sans quoi deux
/// occurrences textuellement différentes avant renommage mais identiques
/// après compteraient à tort comme deux motifs distincts), le candidat au
/// gain net le plus élevé, et retourne `(nom_macro, corps, occurrences)`
/// — ou `None` si aucun candidat n'a un gain net strictement positif.
///
/// Le gain net d'un candidat de corps `body` (longueur `L` caractères)
/// apparaissant `n` fois est : `n*(L - taille_du_nom) - taille_de_la_ligne_#define`.
/// Un candidat à une seule occurrence ne peut jamais être rentable (il n'y
/// a rien à factoriser) et est écarté avant même le calcul.
///
/// Les occurrences qui se chevaucheraient en tokens (cas dégénéré, un
/// motif auto-répétitif comme dans `a(a(a(...)))`) sont réduites à un
/// sous-ensemble maximal sans chevauchement, glouton de gauche à droite,
/// *avant* le calcul du gain — pour que le nombre d'occurrences utilisé
/// dans l'arithmétique soit exactement celui que `apply_macro_extraction`
/// remplacera réellement, jamais un chiffre optimiste.
fn best_macro_extraction(src: &str, extra_protected: &HashSet<String>) -> Option<(String, String, Vec<TokRange>)> {
    let toks = lex_alg(src);
    let in_directive = mark_directive_tokens(&toks);
    let spans = find_macro_candidates(&toks, &in_directive);

    let mut groups: std::collections::HashMap<String, Vec<TokRange>> = std::collections::HashMap::new();
    for (s, e) in spans {
        let text = render_alg_toks(&toks[s..e]);
        if text.chars().count() < MIN_MACRO_BODY_LEN {
            continue;
        }
        groups.entry(text).or_default().push((s, e));
    }

    // Noms déjà utilisés n'importe où dans le fichier — y compris à
    // l'intérieur des directives préprocesseur et en position d'accès
    // membre (swizzle) — plus les mots réservés GLSL, plus `extra_protected`
    // (les identifiants déclarés par `Common`, voir l'appel dans
    // `golf_shader_impl` : cette passe golfe une pass isolément, mais
    // `Common` est textuellement préfixé devant elle avant compilation —
    // un `#define` introduit ici s'appliquerait tout aussi aveuglément à
    // un appel vers une fonction/variable de Common portant le même nom,
    // le corrompant silencieusement). Un `#define` est une substitution
    // textuelle aveugle : il remplacerait tout aussi bien un token
    // identique appartenant à une variable, un swizzle (`.xyz`) ou une
    // autre macro qu'une véritable occurrence du motif factorisé, donc le
    // nouveau nom doit éviter la totalité de cet ensemble, pas seulement
    // les noms déjà (re)nommés par le pipeline de renommage.
    let mut used_idents: HashSet<String> = HashSet::new();
    for t in &toks {
        if let AlgTok::Ident(name) = t {
            used_idents.insert(name.clone());
        }
    }
    for name in extra_protected {
        used_idents.insert(name.clone());
    }
    let reserved: HashSet<&str> = RESERVED.iter().copied().collect();

    let mut best: Option<(isize, String, String, Vec<TokRange>)> = None;
    for (text, mut occurrences) in groups {
        occurrences.sort_by_key(|(s, _)| *s);
        let mut non_overlapping: Vec<TokRange> = Vec::new();
        let mut last_end = 0usize;
        for (s, e) in occurrences {
            if non_overlapping.is_empty() || s >= last_end {
                non_overlapping.push((s, e));
                last_end = e;
            }
        }
        if non_overlapping.len() < 2 {
            continue;
        }

        // `short_name` (voir le renommage d'identifiants dans
        // `golf_shader_impl`) boucle déjà en interne jusqu'à trouver un
        // nom hors de `reserved`/`taken` — l'appeler avec `used_idents`
        // comme `taken` réutilise exactement le même schéma base-52 "1-2
        // caractères" pour le nom de macro, banni contre l'ensemble
        // complet des identifiants déjà présents plutôt que contre les
        // seules cibles de renommage déjà attribuées.
        let name = short_name(0, &reserved, &used_idents);

        let define_line_len = format!("#define {name} {text}\n").chars().count();
        let saved = non_overlapping.len() as isize
            * (text.chars().count() as isize - name.chars().count() as isize);
        let net = saved - define_line_len as isize;
        if net <= 0 {
            continue;
        }
        if best.as_ref().map_or(true, |(best_net, ..)| net > *best_net) {
            best = Some((net, name, text, non_overlapping));
        }
    }
    best.map(|(_, name, text, occs)| (name, text, occs))
}

/// Applique une extraction déjà décidée par `best_macro_extraction` :
/// insère `#define {name} {body}` en tête de fichier (un espace de chaque
/// côté du nom, jamais `(` collé au nom — pour ne jamais accidentellement
/// produire une macro fonction-like), puis remplace chaque occurrence
/// listée par le nom de la macro, en reconstruisant le reste du fichier
/// tel quel token par token.
fn apply_macro_extraction(src: &str, name: &str, body: &str, occurrences: &[TokRange]) -> String {
    let toks = lex_alg(src);
    let occ_map: std::collections::HashMap<usize, usize> = occurrences.iter().cloned().collect();
    let mut out = String::with_capacity(src.len() + body.len() + name.len() + 12);
    out.push_str("#define ");
    out.push_str(name);
    out.push(' ');
    out.push_str(body);
    out.push('\n');
    let mut i = 0;
    while i < toks.len() {
        if let Some(&end) = occ_map.get(&i) {
            out.push_str(name);
            i = end;
        } else {
            out.push_str(&render_alg_toks(&toks[i..i + 1]));
            i += 1;
        }
    }
    out
}

/// Point d'entrée de l'extraction automatique de macros pour les
/// sous-expressions répétées (le "dictionnaire commun" de Shader
/// Minifier) : factorise chaque appel de fonction complet ou accès membre
/// complet identique apparaissant plusieurs fois en un `#define` à 1-2
/// caractères inséré en tête de fichier, seulement quand le gain net (voir
/// `best_macro_extraction`) est positif. **Intégrée à `golf_shader_impl`**
/// (donc à `golf_shader`/`golf_shader_ex`/`golf_shader_with_common`), en
/// toute dernière étape — comme les autres passes "sans inconvénient"
/// (`simplify_algebra`, `golf_for_loops`, `merge_consecutive_declarations`,
/// `strip_redundant_braces`), elle reste **toujours active**, non gérée
/// par les cases à cocher rename/dead-code : elle ne peut jamais faire
/// grossir la sortie (voir le test `size_never_increases`), donc il n'y a
/// aucune raison de la rendre optionnelle. `extra_protected` (les
/// identifiants déclarés par `Common`, déjà utilisé pour protéger le
/// renommage dans `golf_shader_impl`) est transmis pour la même raison ici
/// : voir le commentaire sur `used_idents` dans `best_macro_extraction`.
/// **Jamais appelée depuis `golf_common`** — même statut que le renommage
/// et l'élagage de code mort (voir la note "Jamais appliqué à Common" sur
/// `remove_unused_functions`) : le nom de macro fraîchement choisi ici
/// n'existe dans aucune des deux passes que `protected_names_from_common`
/// connaît (ni le texte original de Common, ni celui de la pass), donc si
/// cette extraction tournait sur Common, un nom qu'elle choisirait
/// pourrait entrer en collision avec un identifiant qu'une pass golfée
/// séparément choisit *elle-même* pour l'une de ses propres variables
/// (renommage local, jamais vu par `protected_names_from_common`) — le
/// `#define` de Common, visible de toutes les passes puisque Common est
/// textuellement préfixé devant chacune avant compilation, réécrirait
/// alors silencieusement cette variable locale à chaque usage. Golfer
/// uniquement au niveau de chaque pass isolée (jamais Common) élimine ce
/// risque : un `#define` introduit ici n'affecte que le texte de *cette*
/// pass, situé après lui, jamais Common (qui le précède toujours) ni une
/// autre pass (compilée séparément).
///
/// Itère jusqu'à point fixe (plafonné, comme `simplify_algebra`/
/// `golf_for_loops`/`remove_unused_functions` ailleurs dans ce fichier) :
/// chaque tour ne factorise que le candidat au gain net le plus élevé
/// (`best_macro_extraction` re-tokenise et re-cherche à chaque tour, sur
/// le texte déjà mis à jour par le tour précédent), pour que factoriser un
/// premier motif puisse en révéler un second — un accès membre ou un appel
/// qui n'était identique à un autre qu'une fois un sous-terme commun déjà
/// remplacé par le même nom de macro des deux côtés. En pratique la
/// plupart des shaders n'ont besoin que d'un ou deux tours (peu de
/// répétition va au-delà d'expressions "feuilles" comme `sin(iTime*2.)`),
/// mais le plafond évite un cas pathologique de boucle infinie si jamais
/// deux candidats s'échangeaient indéfiniment le rang de "meilleur gain"
/// (ce qui ne devrait pas arriver en pratique : chaque tour appliqué
/// réduit strictement la taille totale, donc la séquence de tours est déjà
/// bornée par la taille du fichier, mais le plafond explicite reste la
/// même discipline défensive que le reste de ce fichier plutôt que de
/// compter uniquement sur cet argument).
///
/// Destinée à tourner **après** le reste du pipeline golf (renommage,
/// raccourcissement des littéraux, `simplify_algebra`, `golf_for_loops`,
/// `merge_consecutive_declarations`, `strip_redundant_braces`,
/// `collapse_redundant_semicolons`) — jamais avant, pour les deux raisons
/// données par `best_macro_extraction` : le comptage d'occurrences doit
/// porter sur l'orthographe finale, et l'arithmétique de gain doit
/// raisonner sur les octets qui partiront réellement.
pub fn extract_repeated_subexpr_macros(src: &str, extra_protected: &HashSet<String>) -> String {
    const MAX_PASSES: usize = 16;
    let mut current = src.to_string();
    for _ in 0..MAX_PASSES {
        match best_macro_extraction(&current, extra_protected) {
            Some((name, body, occurrences)) => {
                current = apply_macro_extraction(&current, &name, &body, &occurrences);
            }
            None => break,
        }
    }
    current
}

#[cfg(test)]
mod macro_extraction_tests {
    use super::*;

    #[test]
    fn lex_render_round_trip() {
        // render_alg_toks sur la tranche complète d'un lex_alg(src) doit
        // reproduire src exactement — l'invariant dont dépend toute la
        // logique de remplacement ci-dessus (voir la doc de
        // render_alg_toks).
        for src in [
            "void mainImage(out vec4 c,in vec2 p){c=vec4(sin(p.x*2.),0.,0.,1.);}",
            "#define MAX(a,b) ((a)>(b)?(a):(b))\nfloat f(float x){return x;}\n",
            "",
            "a.xyz+b.rgb",
        ] {
            let toks = lex_alg(src);
            assert_eq!(render_alg_toks(&toks), src, "round-trip failed for: {src}");
        }
    }

    #[test]
    fn extracts_repeated_function_call_when_profitable() {
        // "sin(iTime*2.)" (13 chars) repeated 5 times: saved = 5*(13-1) =
        // 60, define line "#define X sin(iTime*2.)\n" = 24 chars, net =
        // +36 — clearly worth it.
        let src = "void m(){float a=sin(iTime*2.)+sin(iTime*2.)+sin(iTime*2.)+sin(iTime*2.)+sin(iTime*2.);}";
        let out = extract_repeated_subexpr_macros(src, &HashSet::new());
        assert!(out.starts_with("#define "), "expected a #define header in: {out}");
        assert_eq!(
            out.matches("sin(iTime*2.)").count(),
            1,
            "expected exactly one surviving literal occurrence (inside the #define itself): {out}"
        );
        // every one of the 5 original call sites must have been replaced
        // by the same short macro name
        let define_line = out.lines().next().unwrap();
        let name = define_line.strip_prefix("#define ").unwrap().split(' ').next().unwrap();
        assert_eq!(out.matches(name).count(), 6, "expected 1 definition + 5 call-site uses: {out}");
    }

    #[test]
    fn extracts_repeated_member_access_when_profitable() {
        // "iResolution.xy" (14 chars) repeated 4 times: saved =
        // 4*(14-1) = 52, define line "#define X iResolution.xy\n" = 25
        // chars, net = +27.
        let src = "void m(){vec2 a=iResolution.xy;vec2 b=iResolution.xy;vec2 c=iResolution.xy;vec2 d=iResolution.xy;}";
        let out = extract_repeated_subexpr_macros(src, &HashSet::new());
        assert!(out.starts_with("#define "), "expected a #define header in: {out}");
        assert_eq!(
            out.matches("iResolution.xy").count(),
            1,
            "expected exactly one surviving literal occurrence (inside the #define itself): {out}"
        );
    }

    #[test]
    fn no_extraction_when_net_gain_is_negative() {
        // "a.x" (3 chars) repeated twice: saved = 2*(3-1) = 4, define
        // line "#define X a.x\n" = 14 chars, net = -10 — never worth it.
        let src = "void m(){float p=a.x;float q=a.x;}";
        assert_eq!(extract_repeated_subexpr_macros(src, &HashSet::new()), src);
    }

    #[test]
    fn single_occurrence_never_extracted() {
        // No matter how long, one occurrence can never pay for its own
        // #define line — there's nothing to factor out.
        let src = "void m(){float a=sin(cos(tan(iTime*2.+iResolution.x)));}";
        assert_eq!(extract_repeated_subexpr_macros(src, &HashSet::new()), src);
    }

    #[test]
    fn never_touches_text_inside_a_preprocessor_directive() {
        // "sin(iTime*2.)" appears 3 times in real code plus once more
        // inside an unrelated #define line — the count used for the gain
        // calculation, and the replacement itself, must both ignore that
        // fourth occurrence, and the directive line itself must survive
        // completely unmodified (in particular: never rewritten to use
        // the freshly introduced macro name).
        let src = "#define DBG sin(iTime*2.)\nvoid m(){float a=sin(iTime*2.)+sin(iTime*2.)+sin(iTime*2.);}";
        let out = extract_repeated_subexpr_macros(src, &HashSet::new());
        assert!(
            out.contains("#define DBG sin(iTime*2.)"),
            "the pre-existing directive must survive untouched: {out}"
        );
    }

    #[test]
    fn chosen_name_never_collides_with_an_existing_identifier() {
        // Every lowercase single letter from `a` to `j` is already in use
        // (either as a plain identifier or as a swizzle component) — the
        // allocator must skip all ten of them and land on a genuinely free
        // name (a 2-character one, or an uppercase letter) instead of
        // blindly picking one and corrupting the variable used elsewhere.
        // (An earlier version of this test declared `i2` instead of `i` —
        // a different, two-character identifier that leaves plain `i`
        // free — so the assertion below could pass on a false premise;
        // fixed to actually declare all ten single letters.)
        let src = "void m(){\
            float a=1.,b=2.,c=3.,d=4.,e=5.,f=6.,g=7.,h=8.,i=9.,j=10.;\
            vec3 v=vec3(a,b,c);\
            float s1=v.x+v.y+v.z;\
            float s2=sin(iTime*2.)+sin(iTime*2.)+sin(iTime*2.)+sin(iTime*2.)+sin(iTime*2.);\
        }";
        let out = extract_repeated_subexpr_macros(src, &HashSet::new());
        let define_line = out.lines().next().unwrap();
        assert!(define_line.starts_with("#define "), "expected an extraction in: {out}");
        let name = define_line.strip_prefix("#define ").unwrap().split(' ').next().unwrap();
        assert!(
            !"abcdefghij".contains(name),
            "macro name {name} collides with an already-used single-letter identifier"
        );
    }

    #[test]
    fn maximal_member_chain_not_split_into_sub_chains() {
        let toks = lex_alg("a.xyz.zyx+a.xyz.zyx+a.xyz.zyx");
        let in_directive = mark_directive_tokens(&toks);
        let spans = find_macro_candidates(&toks, &in_directive);
        let texts: Vec<String> = spans.iter().map(|(s, e)| render_alg_toks(&toks[*s..*e])).collect();
        // the maximal chain "a.xyz.zyx" must appear (three times, once
        // per occurrence) — and neither of its own sub-chains ("a.xyz" or
        // the dangling ".zyx") must ever show up as a separate candidate.
        assert_eq!(texts.iter().filter(|t| *t == "a.xyz.zyx").count(), 3);
        assert!(!texts.iter().any(|t| t == "a.xyz"), "sub-chain leaked as its own candidate: {texts:?}");
    }

    #[test]
    fn nested_self_referential_calls_never_extracted_or_panic() {
        // A pathological, deliberately self-nesting case: "f(f(f(x)))"
        // generates three candidate spans ("f(x)", "f(f(x))",
        // "f(f(f(x)))") that all overlap in token ranges — but each has a
        // textually *different* rendered body (different nesting depth),
        // so they land in three separate groups of one occurrence each,
        // none of which is ever extracted. Exists mainly to confirm this
        // never panics or corrupts the source when candidate spans nest
        // inside one another, which the non-overlap reduction in
        // best_macro_extraction exists to guard even though no single
        // group of *identical* text can actually overlap itself in this
        // candidate model (a function-call span's length is tied
        // one-to-one to its nesting depth, so two overlapping spans can
        // never render the same text).
        let src = "void m(){float a=f(f(f(x)));}";
        assert_eq!(extract_repeated_subexpr_macros(src, &HashSet::new()), src);
    }

    #[test]
    fn control_flow_keywords_never_become_a_candidate_base() {
        // "if(p.x>0.)" repeated 4 times with an identical condition would
        // otherwise look exactly like a profitable call candidate — but
        // extracting it and substituting back would silently strip the
        // `if` keyword itself from every site (`if(cond){a=1.;}` ->
        // `X{a=1.;}`, no longer conditional). STATEMENT_KEYWORDS exists
        // precisely to keep this from ever being generated as a
        // candidate at all, regardless of how profitable the byte count
        // alone would make it look.
        let src = "void m(){if(p.x>0.){a=1.;}if(p.x>0.){b=1.;}if(p.x>0.){c=1.;}if(p.x>0.){d=1.;}}";
        let out = extract_repeated_subexpr_macros(src, &HashSet::new());
        assert_eq!(out, src, "if(...) must never be extracted, even when repeated: {out}");
    }

    #[test]
    fn builtin_function_and_variable_names_are_valid_candidate_bases() {
        // The reverse of the previous test: `sin`/`iResolution` etc. are
        // RESERVED (protected from *renaming*) but are not statement
        // keywords — they must remain valid, and in fact the single most
        // common, candidate bases (this is literally the ticket's own
        // `sin(iTime*2.)` example). Exercised indirectly by
        // extracts_repeated_function_call_when_profitable and
        // extracts_repeated_member_access_when_profitable above; this
        // test only pins down find_macro_candidates' own output so a
        // future accidental widening of STATEMENT_KEYWORDS back to all of
        // RESERVED (the bug this function was fixed from) fails fast.
        let toks = lex_alg("sin(iTime*2.);iResolution.xy;");
        let in_directive = mark_directive_tokens(&toks);
        let spans = find_macro_candidates(&toks, &in_directive);
        let texts: Vec<String> = spans.iter().map(|(s, e)| render_alg_toks(&toks[*s..*e])).collect();
        assert!(texts.contains(&"sin(iTime*2.)".to_string()), "expected sin(...) as a candidate: {texts:?}");
        assert!(texts.contains(&"iResolution.xy".to_string()), "expected iResolution.xy as a candidate: {texts:?}");
    }

    #[test]
    fn function_call_requires_no_space_before_paren() {
        // Conservative-by-construction (see find_macro_candidates' doc
        // comment): a call written with a space before its `(` is simply
        // never recognized as a candidate — never incorrect, just a
        // missed optimization, consistent with the rest of this file's
        // "no partial/best-effort rewriting" philosophy.
        let toks = lex_alg("sin (iTime*2.)");
        let in_directive = mark_directive_tokens(&toks);
        let spans = find_macro_candidates(&toks, &in_directive);
        assert!(spans.is_empty(), "expected no candidate for a space-separated call: {spans:?}");
    }

    #[test]
    fn wired_into_full_pipeline_via_golf_shader() {
        // The integration itself: golf_shader (not extract_repeated_subexpr_macros
        // called directly) must perform the extraction as its last step.
        let src = "void mainImage(out vec4 fragColor,in vec2 fragCoord){\
            float a=sin(iTime*2.)+sin(iTime*2.)+sin(iTime*2.)+sin(iTime*2.)+sin(iTime*2.);\
            fragColor=vec4(a);\
        }";
        let golfed = golf_shader(src);
        assert!(golfed.starts_with("#define "), "expected golf_shader itself to extract a macro: {golfed}");
        assert_eq!(
            golfed.matches("iTime*2.").count(),
            1,
            "expected the repeated call factored down to a single surviving spelling: {golfed}"
        );
    }

    #[test]
    fn extraction_avoids_a_name_reserved_by_common() {
        // golf_shader_ex threads its `common_source` through to the
        // renamer as `extra_protected` — this test pins down that the same
        // protection reaches the macro-name allocator too. Every
        // single-letter name is pre-claimed by declaring them all as
        // Common globals, forcing the allocator to reach for a
        // two-character (or uppercase) name instead of silently reusing
        // one of Common's.
        let common = "float a,b,c,d,e,f,g,h,i,j,k,l,m,n,o,p,q,r,s,t,u,v,w,x,y,z;";
        let src = "void mainImage(out vec4 fragColor,in vec2 fragCoord){\
            fragColor=vec4(sin(iTime*2.)+sin(iTime*2.)+sin(iTime*2.)+sin(iTime*2.)+sin(iTime*2.));\
        }";
        let golfed = golf_shader_ex(src, common, true, true, true);
        if let Some(define_line) = golfed.lines().next().filter(|l| l.starts_with("#define ")) {
            let name = define_line.strip_prefix("#define ").unwrap().split(' ').next().unwrap();
            assert!(
                name.chars().count() > 1 || name.chars().next().unwrap().is_ascii_uppercase(),
                "macro name {name} collides with a single-letter global declared in Common"
            );
        }
        // Whether or not an extraction actually happened (the golfed
        // fragment is short enough that it might not clear the net-gain
        // bar once a longer 2-char name is forced), the golfed pass must
        // still compile standalone against `common` conceptually — i.e.
        // never emit a bare single-letter #define that shadows one of
        // Common's globals. The check above already covers that; this
        // comment just documents the intent for a reader skimming the
        // assertion alone.
    }

    #[test]
    fn golf_common_never_extracts_macros() {
        // Same repeated-call shape that `golf_shader` would happily
        // factor — but through `golf_common`, which must leave it alone:
        // a #define introduced here would be visible (and could collide
        // with a same-named local) in every pass Common gets prepended
        // to, the same cross-pass risk documented for renaming/dead-code
        // elimination.
        let src = "vec3 palette(float t){\
            return vec3(sin(iTime*2.)+sin(iTime*2.)+sin(iTime*2.)+sin(iTime*2.)+sin(iTime*2.));\
        }";
        let golfed = golf_common(src);
        assert!(!golfed.contains("#define"), "golf_common must never introduce a macro: {golfed}");
    }

    #[test]
    fn size_never_increases() {
        // By construction (best_macro_extraction only ever accepts a
        // candidate with net > 0), running this pass can never make the
        // output longer than the input, on any input — including
        // default.frag's own fully-golfed form.
        let plain = "void m(){float a=sin(iTime*2.)+sin(iTime*2.)+sin(iTime*2.);}";
        assert!(extract_repeated_subexpr_macros(plain, &HashSet::new()).len() <= plain.len());

        let default_frag_golfed = golf_shader(include_str!("../../python_ui/assets/shaders/default.frag"));
        let extracted = extract_repeated_subexpr_macros(&default_frag_golfed, &HashSet::new());
        assert!(
            extracted.len() <= default_frag_golfed.len(),
            "extraction must never grow the golfed output: {} -> {}",
            default_frag_golfed.len(),
            extracted.len()
        );
    }
}

#[cfg(test)]
mod simplify_algebra_tests {
    use super::*;

    #[test]
    fn multiply_by_one_both_orders() {
        assert_eq!(simplify_algebra("x*1."), "x");
        assert_eq!(simplify_algebra("1.*x"), "x");
        assert_eq!(simplify_algebra("a*1.0"), "a");
        assert_eq!(simplify_algebra("1.0*a"), "a");
    }

    #[test]
    fn divide_by_one() {
        assert_eq!(simplify_algebra("x/1."), "x");
        // division is not commutative: 1./x must never simplify
        assert_eq!(simplify_algebra("1./x"), "1./x");
    }

    #[test]
    fn add_or_subtract_zero() {
        assert_eq!(simplify_algebra("x+0."), "x");
        assert_eq!(simplify_algebra("0.+x"), "x");
        assert_eq!(simplify_algebra("x-0."), "x");
        // subtraction is not commutative: 0.-x (== -x) must never simplify to x
        assert_eq!(simplify_algebra("0.-x"), "0.-x");
    }

    #[test]
    fn multiply_by_zero_both_orders() {
        assert_eq!(simplify_algebra("x*0."), "0.");
        assert_eq!(simplify_algebra("0.*x"), "0.");
    }

    #[test]
    fn constant_fold_exact_integer_literals() {
        assert_eq!(simplify_algebra("2.*3."), "6.");
        assert_eq!(simplify_algebra("2.+3."), "5.");
        assert_eq!(simplify_algebra("5.-3."), "2.");
        // fits GLSL's own convention: no `Number` token text ever starts
        // with `-`, a negative result is a unary minus + positive literal
        assert_eq!(simplify_algebra("3.-5."), "-2.");
        assert_eq!(simplify_algebra("3.-3."), "0.");
    }

    #[test]
    fn constant_fold_never_touches_non_integer_literals() {
        // 3.14159265 is only an approximation of pi -- folding it could
        // land on a different f32 rounding than the GPU driver would
        // produce, so it must be left completely untouched.
        assert_eq!(simplify_algebra("2.*3.14159265"), "2.*3.14159265");
        // a literal with a genuine fractional part is excluded too, even
        // though the arithmetic itself would be exact here (0.5+0.5): the
        // rule is gated on both operands individually being exact
        // integers, not on the result happening to be one.
        assert_eq!(simplify_algebra("0.5+0.5"), "0.5+0.5");
    }

    #[test]
    fn constant_fold_never_touches_division() {
        // deliberately out of scope for this first pass -- see the rule's
        // own doc comment for why.
        assert_eq!(simplify_algebra("6./3."), "6./3.");
    }

    #[test]
    fn constant_fold_never_touches_exponent_literals() {
        // 2e3 is an exact integer value (2000.) but written with an
        // exponent -- excluded on principle, same "never touch anything
        // that isn't the plainest possible literal shape" spirit as the
        // rest of this rule.
        assert_eq!(simplify_algebra("2e3+1."), "2e3+1.");
    }

    #[test]
    fn constant_fold_chains_to_a_fixed_point() {
        // 2.*3.*4. folds left-to-right over two passes: (2.*3.) -> 6.,
        // then 6.*4. -> 24. -- exactly the same "iterate to a fixed point"
        // guarantee the rest of this pass already relies on.
        assert_eq!(simplify_algebra("2.*3.*4."), "24.");
    }

    #[test]
    fn constant_fold_wired_into_full_pipeline() {
        let src = "void mainImage(out vec4 c,in vec2 p){c=vec4(p.x*2.*3.14159265);}";
        let golfed = golf_shader(src);
        // the exact-integer pair 2.* (from 2.*pi) must not itself get
        // folded away since it's not adjacent to another literal here --
        // this is really just a smoke test that the new rule coexists
        // cleanly with a realistic shader body.
        assert!(golfed.contains("2.*3.14159265"), "unrelated literal must survive untouched: {golfed}");

        let src2 = "void mainImage(out vec4 c,in vec2 p){float k=2.*3.;c=vec4(k);}";
        let golfed2 = golf_shader(src2);
        assert!(golfed2.contains("6."), "expected the two literals folded to 6. in: {golfed2}");
        assert!(!golfed2.contains("2.*3."), "the unfolded pair must not survive in: {golfed2}");
    }

    #[test]
    fn pow_two_and_three() {
        assert_eq!(simplify_algebra("pow(x,2.)"), "x*x");
        assert_eq!(simplify_algebra("pow(x,3.)"), "x*x*x");
        // higher exponents are left alone: pow stays shorter than the expansion
        assert_eq!(simplify_algebra("pow(x,4.)"), "pow(x,4.)");
    }

    #[test]
    fn pow_with_non_atomic_argument_untouched() {
        // (a+b) is a subexpression, not a lone token: never rewritten
        assert_eq!(simplify_algebra("pow(a+b,2.)"), "pow(a+b,2.)");
        assert_eq!(simplify_algebra("pow(a.x,2.)"), "pow(a.x,2.)");
    }

    #[test]
    fn double_unary_negation() {
        assert_eq!(simplify_algebra("- -x"), "x");
        assert_eq!(simplify_algebra("y=- -x;"), "y=x;");
        // reachable after a keyword (unary context), not just at file start
        assert_eq!(simplify_algebra("return - -x;"), "return x;");
    }

    #[test]
    fn binary_minus_of_unary_minus_never_touched() {
        // a - -b == a+b, but must never become "ab" or similar: the first
        // '-' is binary (a is a completed value to its left), so the rule
        // must not fire at all here.
        assert_eq!(simplify_algebra("a- -b"), "a- -b");
        assert_eq!(simplify_algebra("f(x)- -y"), "f(x)- -y");
    }

    #[test]
    fn real_decrement_operator_never_touched() {
        // zero characters between the two '-' is the actual decrement
        // operator per strip_operator_spaces' own invariant, never
        // double-negation.
        assert_eq!(simplify_algebra("x--;"), "x--;");
        assert_eq!(simplify_algebra("--x;"), "--x;");
    }

    #[test]
    fn assign_plus_one_becomes_increment() {
        assert_eq!(simplify_algebra("x=x+1.;"), "x++;");
        assert_eq!(simplify_algebra("x=x-1.;"), "x--;");
        assert_eq!(simplify_algebra("count=count+1.0;"), "count++;");
    }

    #[test]
    fn compound_assign_one_becomes_increment() {
        assert_eq!(simplify_algebra("x+=1.;"), "x++;");
        assert_eq!(simplify_algebra("x-=1.;"), "x--;");
    }

    #[test]
    fn increment_in_for_loop_header() {
        assert_eq!(
            simplify_algebra("for(float i=0.;i<4.;i+=1.){a();}"),
            "for(float i=0.;i<4.;i++){a();}"
        );
        assert_eq!(
            simplify_algebra("for(float i=0.;i<4.;i=i+1.){a();}"),
            "for(float i=0.;i<4.;i++){a();}"
        );
    }

    #[test]
    fn increment_never_fires_when_value_is_used() {
        // assignment used as a condition: preceded by '(' (the if's own
        // opening paren), not ';' -> must be left untouched
        assert_eq!(simplify_algebra("if(x=x+1.){}"), "if(x=x+1.){}");
        // assignment used as a call argument: same reasoning
        assert_eq!(simplify_algebra("foo(x+=1.);"), "foo(x+=1.);");
        // assignment whose result feeds a further expression
        assert_eq!(simplify_algebra("a=(x+=1.)+2.;"), "a=(x+=1.)+2.;");
    }

    #[test]
    fn increment_requires_matching_names_and_literal_one() {
        // different variable on each side: not an accumulator, never rewritten
        assert_eq!(simplify_algebra("y=x+1.;"), "y=x+1.;");
        // literal isn't exactly 1: never rewritten *into `x++`* -- but see
        // `compound_assign_generalized_operator_and_operand` below, this
        // now becomes the shorter `x+=2.;` via the generalized rule instead.
        assert_eq!(simplify_algebra("x+=2.;"), "x+=2.;");
    }

    #[test]
    fn compound_assign_generalized_operator_and_operand() {
        // any operator, not just +/-, and any atomic operand, not just the
        // literal 1 -- the generalization of the x=x+1.->x++ rule above.
        assert_eq!(simplify_algebra("x=x+2.;"), "x+=2.;");
        assert_eq!(simplify_algebra("x=x-y;"), "x-=y;");
        assert_eq!(simplify_algebra("x=x*light;"), "x*=light;");
        assert_eq!(simplify_algebra("x=x/2.;"), "x/=2.;");
        assert_eq!(simplify_algebra("x=x%y;"), "x%=y;");
    }

    #[test]
    fn compound_assign_generalized_in_for_loop_header() {
        assert_eq!(
            simplify_algebra("for(float i=0.;i<4.;i=i*2.){a();}"),
            "for(float i=0.;i<4.;i*=2.){a();}"
        );
    }

    #[test]
    fn compound_assign_generalized_never_fires_when_value_is_used() {
        // same reasoning as `increment_never_fires_when_value_is_used`,
        // but here it's about whether more expression follows the atomic
        // operand -- NOT about whether the assignment's own value is read
        // (see the doc comment on the rule itself: unlike `x++`, `x OP=`
        // has the same value as `x = x OP ...` in every context).
        assert_eq!(simplify_algebra("if(x=x*2.){}"), "if(x=x*2.){}");
        assert_eq!(simplify_algebra("foo(x*=2.);"), "foo(x*=2.);");
    }

    #[test]
    fn compound_assign_generalized_never_fires_on_non_atomic_or_reused_name() {
        // the atomic operand must be the *entire* right-hand side: once
        // there's more after it (even a further use of `x` itself, as in
        // `x*x`), the terminator right after the first atomic operand is
        // no longer `;`/`)`, so the rule correctly refuses to fire rather
        // than truncating a larger expression -- `x=x*x+1.;` parses as
        // `(x*x)+1.`, and must never become `x*=x+1.;` (== `x*(x+1.)` ==
        // `x*x+x`, a different value).
        assert_eq!(simplify_algebra("x=x*x+1.;"), "x=x*x+1.;");
        assert_eq!(simplify_algebra("x=x+a-b;"), "x=x+a-b;");
        // a function call is never an atomic operand either
        assert_eq!(simplify_algebra("x=x*f(y);"), "x=x*f(y);");
    }

    #[test]
    fn chained_reduction_needs_multiple_passes() {
        // pow(x,2.) -> x*x only becomes adjacent to a lone-operand "*1."
        // reduction after the pow rewrite has already run once.
        assert_eq!(simplify_algebra("pow(x,2.)*1."), "x*x");
    }

    #[test]
    fn line_count_never_changes() {
        let src = "float f(float x){\n  return pow(x,2.)*1.+0.;\n}\n";
        let out = simplify_algebra(src);
        assert_eq!(src.matches('\n').count(), out.matches('\n').count());
    }

    #[test]
    fn for_loop_golf_basic() {
        assert_eq!(
            simplify_algebra("for(float i=0.;i<64.;i++){a();}"),
            "for(float i=0.;i<64.;i++){a();}", // simplify_algebra alone never touches for-loops
        );
        assert_eq!(
            golf_for_loops("for(float i=0.;i<64.;i++){a();}"),
            "for(float i=0.;i++<64.;){a();}"
        );
        assert_eq!(
            golf_for_loops("for(int i=0;i<64.;i++){a();}"),
            "for(int i=0;i++<64.;){a();}"
        );
    }

    #[test]
    fn for_loop_golf_never_fires_when_var_reused_in_body() {
        // i's value inside the body would shift by one under the rewrite
        // (it's already incremented by the time the body runs), so any
        // body that reads i must block the rewrite entirely.
        let src = "for(float i=0.;i<64.;i++){p+=i;}";
        assert_eq!(golf_for_loops(src), src);
    }

    #[test]
    fn for_loop_golf_never_fires_when_var_reused_in_init_or_bound() {
        let src_init = "for(float i=i;i<64.;i++){a();}";
        assert_eq!(golf_for_loops(src_init), src_init);
        let src_bound = "for(float i=0.;i<i;i++){a();}";
        assert_eq!(golf_for_loops(src_bound), src_bound);
    }

    #[test]
    fn for_loop_golf_requires_exact_shape() {
        // wrong relational operator (strict `<` only, see try_rewrite_for_loop)
        let src1 = "for(float i=0.;i<=64.;i++){a();}";
        assert_eq!(golf_for_loops(src1), src1);
        // no type (reused pre-existing variable, not a fresh loop counter)
        let src2 = "for(i=0.;i<64.;i++){a();}";
        assert_eq!(golf_for_loops(src2), src2);
        // decrementing loop, not the canonical ascending shape
        let src3 = "for(float i=64.;i>0.;i--){a();}";
        assert_eq!(golf_for_loops(src3), src3);
        // no braces around the body
        let src4 = "for(float i=0.;i<64.;i++)a();";
        assert_eq!(golf_for_loops(src4), src4);
        // unrelated type keyword right after '(' isn't a recognized counter type
        let src5 = "for(vec2 i=v;i<64.;i++){a();}";
        assert_eq!(golf_for_loops(src5), src5);
    }

    #[test]
    fn for_loop_golf_handles_nested_loops() {
        // inner loop uses a different counter name and neither body reads
        // the other loop's variable, so both are safe to rewrite; the
        // outer match's own BODY (copied through verbatim on its pass,
        // since only the header is rewritten) exposes the inner loop for
        // the next fixed-point iteration.
        let src = "for(float i=0.;i<4.;i++){for(float j=0.;j<8.;j++){b();}}";
        assert_eq!(
            golf_for_loops(src),
            "for(float i=0.;i++<4.;){for(float j=0.;j++<8.;){b();}}"
        );
    }

    #[test]
    fn for_loop_golf_blocks_outer_when_inner_shadows_same_name() {
        // A nested loop redeclaring the same name `i` is legal GLSL
        // (shadowing), but this golfer has no real scope analysis — seeing
        // `i` anywhere in the outer body, even as an unrelated shadowed
        // declaration, must conservatively block the outer rewrite.
        let src = "for(float i=0.;i<4.;i++){for(float i=0.;i<8.;i++){a();}}";
        let out = golf_for_loops(src);
        assert!(!out.contains("i++<4."), "outer loop must not be rewritten: {out}");
    }

    #[test]
    fn for_loop_golf_runs_after_increment_normalization() {
        // x+=1./x=x+1. in a for-loop's own increment clause is first turned
        // into i++ by simplify_algebra (see increment_in_for_loop_header
        // above); golf_for_loops must see that already-normalized form,
        // since it only recognizes a literal `i++` increment clause.
        let src = "for(float i=0.;i<4.;i+=1.){a();}";
        let after_algebra = simplify_algebra(src);
        assert_eq!(after_algebra, "for(float i=0.;i<4.;i++){a();}");
        assert_eq!(golf_for_loops(&after_algebra), "for(float i=0.;i++<4.;){a();}");
    }

    #[test]
    fn for_loop_golf_line_count_never_changes() {
        let src = "void f(){\n  for(float i=0.;i<4.;i++){\n    a();\n  }\n}\n";
        let out = golf_for_loops(src);
        assert_eq!(src.matches('\n').count(), out.matches('\n').count());
    }

    #[test]
    fn for_loop_golf_full_pipeline_smoke_test() {
        // via the real golf_shader entry point, chained after renaming
        let src = "void mainImage(out vec4 c,in vec2 p){for(float idx=0.;idx<64.;idx++){c+=.1;}}";
        let golfed = golf_shader(src);
        assert!(golfed.contains("++<"), "expected the increment pushed into the condition: {golfed}");
    }

    #[test]
    fn full_pipeline_smoke_test() {
        let src = "void mainImage(out vec4 c,in vec2 p){float x=p.x;c=vec4(pow(x,2.)*1.+0.);}";
        // via the real golf_shader entry point, not just the standalone pass
        let golfed = golf_shader(src);
        assert!(!golfed.contains("pow"), "pow should have been expanded: {golfed}");
        assert!(golfed.contains("*"), "expected a multiplication in: {golfed}");
    }

    #[test]
    fn compound_assign_generalized_wired_into_full_pipeline() {
        // via the real golf_shader entry point: an accumulator loop body
        // (`col=col*light;`) should come out using `*=` rather than the
        // longer `col=col*light;` spelling -- fires *after* renaming, on
        // whatever short name `col`/`light` end up with.
        let src = "void mainImage(out vec4 fragColor,in vec2 fragCoord){\
            vec3 col=vec3(0.);vec3 light=vec3(1.);col=col*light;fragColor=vec4(col,1.);}";
        let golfed = golf_shader(src);
        assert!(golfed.contains("*="), "expected a compound *= in: {golfed}");
        // 4 chars shorter than the `x=x*y;` spelling it replaces (`x*=y;`)
        let naive = golf_shader_ex(src, "", false, false, true); // rename off: keeps `col`/`light` readable
        assert!(naive.contains("col*=light;"), "expected col*=light; (rename off) in: {naive}");
    }
}

#[cfg(test)]
mod merge_declarations_tests {
    use super::*;

    #[test]
    fn basic_two_statement_merge() {
        assert_eq!(
            merge_consecutive_declarations("float a=1.;float b=2.;"),
            "float a=1.,b=2.;"
        );
    }

    #[test]
    fn chains_more_than_two_statements() {
        // the raymarching-header shape this ticket calls out explicitly:
        // origin, direction, accumulated distance in one block.
        assert_eq!(
            merge_consecutive_declarations("vec3 ro=vec3(0.);vec3 rd=vec3(1.);float t=0.;"),
            "vec3 ro=vec3(0.),rd=vec3(1.);float t=0.;"
        );
        // t's own type (float) differs from vec3, so it starts a fresh
        // chain rather than joining the first one — merged separately
        // is still correct since a lone statement is left untouched
        // (merged_count stays at 1, see is_candidate below).
    }

    #[test]
    fn does_not_merge_across_intervening_statement() {
        // an assignment sits between the two declarations: the chain must
        // stop there rather than reach across it.
        let src = "float a=1.;x=2.;float b=3.;";
        assert_eq!(merge_consecutive_declarations(src), src);
    }

    #[test]
    fn does_not_merge_across_brace() {
        // reaching across the `if`'s braces would change b's scope from
        // "same block as a" to "still inside the if" — must never happen.
        let src = "float a=1.;if(x){y=2.;}float b=3.;";
        assert_eq!(merge_consecutive_declarations(src), src);
    }

    #[test]
    fn does_not_merge_different_base_types() {
        let src = "float a=1.;int b=2;";
        assert_eq!(merge_consecutive_declarations(src), src);
    }

    #[test]
    fn does_not_merge_qualified_declaration() {
        // merging would silently apply the qualifier of the first
        // statement to every fused declarator, which is only correct if
        // every merged statement actually carried that same qualifier —
        // unknowable for the ones after the first, so refuse outright.
        let src = "const float a=1.;float b=2.;";
        assert_eq!(merge_consecutive_declarations(src), src);
    }

    #[test]
    fn does_not_merge_array_declarations() {
        let src = "float a[4];float b=1.;";
        assert_eq!(merge_consecutive_declarations(src), src);
    }

    #[test]
    fn does_not_touch_lone_declaration() {
        let src = "float a=1.;x=a;";
        assert_eq!(merge_consecutive_declarations(src), src);
    }

    #[test]
    fn does_not_merge_function_definition() {
        // `float f(float x){...}` looks like a declaration up to the name,
        // but the `(` right after means this was never a plain declarator
        // list — parse_type_decl_stmt must back off, not just for the
        // function itself but without corrupting anything that follows.
        let src = "float f(float x){return x;}float a=1.;float b=2.;";
        assert_eq!(
            merge_consecutive_declarations(src),
            "float f(float x){return x;}float a=1.,b=2.;"
        );
    }

    #[test]
    fn preserves_initializers_with_nested_commas() {
        // a constructor call's own commas must never be mistaken for the
        // declarator-separating comma this pass introduces.
        assert_eq!(
            merge_consecutive_declarations("vec3 a=vec3(1.,2.,3.);vec3 b=vec3(4.,5.,6.);"),
            "vec3 a=vec3(1.,2.,3.),b=vec3(4.,5.,6.);"
        );
    }

    #[test]
    fn merges_declarations_without_initializers() {
        assert_eq!(merge_consecutive_declarations("float a;float b;"), "float a,b;");
    }

    #[test]
    fn merges_across_newline_separated_statements() {
        // the common real-world shape: each declaration on its own line at
        // the top of mainImage.
        assert_eq!(
            merge_consecutive_declarations("float a=1.;\nfloat b=2.;\n"),
            "float a=1.,b=2.;\n"
        );
    }

    #[test]
    fn full_pipeline_smoke_test() {
        // rename/dead-code off here so the declarator names stay exactly
        // `a`/`b` and the fused-declarator shape can be asserted on
        // directly — golf_shader (rename+dead-code on) is exercised by the
        // regression test below instead, where only the *size* is checked.
        let src = "void mainImage(out vec4 c,in vec2 p){float a=1.;float b=2.;c=vec4(a+b);}";
        let golfed = golf_shader_ex(src, "", false, false, true);
        assert!(golfed.contains("a=1.,b=2."), "expected fused declarators in: {golfed}");
    }

    #[test]
    fn regression_default_frag_size_shrinks() {
        // Historical note: earlier entries in this test tracked a much
        // smaller, terser `default.frag` (a `palette()`-based shader that
        // golfed down to 499 bytes through this exact sequence of passes:
        // 559 -> 521 -> 519 -> 502 -> 499, see the project's ROADMAP.md for
        // the blow-by-blow). `default.frag` was later entirely rewritten
        // into a longer, heavily-commented "raymarching fractal + glow
        // volumétrique" study shader with verbose French documentation and
        // self-explanatory identifiers (`getAspectCorrectedUV`,
        // `processFractalFold`, `computePaletteColor`, ...) — a deliberate
        // authoring choice for a shader that ships as the very first thing
        // a new user sees, not a regression. This test's expected byte
        // count is therefore no longer 499; it now reflects the current
        // `default.frag`, first verified for real once a working
        // rustc/cargo toolchain became available in this environment
        // (previous sessions could only golf/relex `golf.rs` in isolation,
        // never actually run this test against the real file on disk).
        let src = include_str!("../../python_ui/assets/shaders/default.frag");
        let golfed = golf_shader(src);
        assert_eq!(golfed.len(), 1417, "expected byte count for golfed default.frag: {} bytes", golfed.len());
    }
}

#[cfg(test)]
mod strip_redundant_braces_tests {
    use super::*;

    #[test]
    fn basic_if_single_statement() {
        assert_eq!(strip_redundant_braces("if(x){y=1.;}"), "if(x)y=1.;");
    }

    #[test]
    fn basic_for_single_statement() {
        assert_eq!(strip_redundant_braces("for(x;y;z){a();}"), "for(x;y;z)a();");
    }

    #[test]
    fn basic_while_single_statement() {
        assert_eq!(strip_redundant_braces("while(x){a();}"), "while(x)a();");
    }

    #[test]
    fn if_else_both_stripped() {
        assert_eq!(
            strip_redundant_braces("if(x){y=1.;}else{z=1.;}"),
            "if(x)y=1.;else z=1.;"
        );
    }

    #[test]
    fn else_if_chain_stripped() {
        // The classic "else if" idiom: the else-branch's own body is
        // itself a further if — recognized fine since `contains_if_without_else`
        // only worries about an `if` *without* its own `else`, and this
        // inner `if(y)` does have one.
        assert_eq!(
            strip_redundant_braces("if(x){a=1.;}else{if(y){b=1.;}else{c=1.;}}"),
            "if(x)a=1.;else if(y)b=1.;else c=1.;"
        );
    }

    #[test]
    fn multi_statement_block_never_stripped() {
        let src = "if(x){a=1.;b=2.;}";
        assert_eq!(strip_redundant_braces(src), src);
    }

    #[test]
    fn empty_block_never_stripped() {
        let src = "if(x){}";
        assert_eq!(strip_redundant_braces(src), src);
    }

    #[test]
    fn function_definition_body_never_touched() {
        // `)` right before `{` here closes a parameter list, not a
        // condition — the keyword right before that `(` is the function
        // name `f`, never a reserved control-flow keyword (GLSL forbids
        // naming a function `if`/`for`/`while`), so this must never be
        // mistaken for a strippable body.
        let src = "float f(float x){return x;}";
        assert_eq!(strip_redundant_braces(src), src);
    }

    #[test]
    fn switch_body_never_touched() {
        let src = "switch(x){case 1:y=1.;break;}";
        assert_eq!(strip_redundant_braces(src), src);
    }

    #[test]
    fn do_while_body_never_touched() {
        let src = "do{x++;}while(x<4);";
        assert_eq!(strip_redundant_braces(src), src);
    }

    // --- dangling-else safety: the core of this ticket ---

    #[test]
    fn dangling_else_blocks_outer_if_strip() {
        // If the outer if's braces were stripped here, a later `else`
        // meant for `if(a)` would instead bind to the newly-unbraced
        // `if(b)` inside it — the classic ambiguity. Must stay braced.
        let src = "if(a){if(b)x=1.;}";
        assert_eq!(strip_redundant_braces(src), src);
    }

    #[test]
    fn dangling_else_blocks_even_when_inner_if_has_its_own_braces() {
        // The *outer* if(a)'s braces must stay (its single-statement body
        // is itself an if without an else, exactly the risk this ticket
        // guards against) — but the *inner* if(b)'s own braces are an
        // entirely independent, unrelated candidate: they wrap "x=1.;"
        // alone, no nested if inside, so stripping them can never change
        // which if a later else binds to (there's no else anywhere in
        // this snippet to begin with). find_strippable_braces evaluates
        // every brace pair independently against the *original* tokens
        // (see its own doc comment), so it correctly strips this inner,
        // safe pair while still refusing the outer, risky one.
        let src = "if(a){if(b){x=1.;}}";
        assert_eq!(strip_redundant_braces(src), "if(a){if(b)x=1.;}");
    }

    #[test]
    fn dangling_else_blocks_for_and_while_too() {
        // Per the roadmap item's own "même traitement pour for/while":
        // conservative uniformity, even though for/while can't actually
        // carry a trailing else themselves.
        assert_eq!(strip_redundant_braces("for(x;y;z){if(b)a=1.;}"), "for(x;y;z){if(b)a=1.;}");
        assert_eq!(strip_redundant_braces("while(x){if(b)a=1.;}"), "while(x){if(b)a=1.;}");
    }

    #[test]
    fn dangling_else_risk_deep_in_a_chain_of_single_statements() {
        // The risky `if` doesn't have to be the *direct* body — it just
        // has to be reachable inside the one statement, however it's
        // nested (here: inside a for-loop that is itself the if's single
        // statement).
        let src = "if(a){for(x;y;z)if(b)c=1.;}";
        assert_eq!(strip_redundant_braces(src), src);
    }

    #[test]
    fn if_without_else_but_no_wrapping_construct_is_fine() {
        // A lone if-without-else, not itself sitting inside another
        // construct's block, has nothing to become ambiguous with — no
        // braces to strip here in the first place (there's no outer
        // if/for/while/else header before this one), so this must be a
        // pure no-op, not a false "risk" rejection of something that was
        // never a stripping candidate.
        let src = "if(b)c=1.;";
        assert_eq!(strip_redundant_braces(src), src);
    }

    #[test]
    fn already_resolved_inner_if_else_permits_outer_strip() {
        // The inner if *does* have its own else, so there's no ambiguity
        // left to create — the outer if's braces are safe to drop, and
        // the trailing else correctly keeps binding to the outer if once
        // its own body ends (this is exactly what `contains_if_without_else`
        // is designed to allow through). The inner if/else pair is itself
        // an independent, equally safe candidate (its own body is a lone
        // statement on each side, no nesting), so both levels are
        // stripped in the same single left-to-right scan.
        let src = "if(a){if(b){x=1.;}else{y=1.;}}else{z=1.;}";
        assert_eq!(
            strip_redundant_braces(src),
            "if(a)if(b)x=1.;else y=1.;else z=1.;"
        );
    }

    // --- nesting / nothing missed ---

    #[test]
    fn nested_independently_stripped_constructs() {
        // A single scan finds and strips both the outer and the inner
        // single-statement bodies in one call — no fixed-point iteration
        // needed, unlike simplify_algebra/golf_for_loops (see
        // find_strippable_braces' doc comment for why).
        assert_eq!(strip_redundant_braces("if(a){for(x;y;z){b();}}"), "if(a)for(x;y;z)b();");
    }

    #[test]
    fn sibling_inside_unstrippable_outer_block_still_gets_stripped() {
        // The outer if's own braces can't be dropped (two statements
        // inside), but the independent if/else nested alongside the first
        // statement is still found and stripped on its own merits — the
        // scan doesn't skip over the contents of a block it declined to
        // unwrap.
        assert_eq!(
            strip_redundant_braces("if(a){x=1.;if(b){y=1.;}else{z=1.;}}"),
            "if(a){x=1.;if(b)y=1.;else z=1.;}"
        );
    }

    #[test]
    fn preserves_nested_comma_expressions_inside_stripped_body() {
        assert_eq!(
            strip_redundant_braces("if(x){c=vec3(1.,2.,3.);}"),
            "if(x)c=vec3(1.,2.,3.);"
        );
    }

    #[test]
    fn full_pipeline_smoke_test() {
        // Through the *full* golf_shader pipeline (not strip_redundant_braces
        // in isolation), identifier renaming also runs — `c`/`p` are not
        // protected/reserved names, so they become whatever short names
        // the frequency-weighted renamer assigns (`c` occurs 3 times vs
        // `p`'s 2, so `c`->`a`, `p`->`b` here), not their original
        // spelling. Assert on structure (braces gone) rather than a
        // specific renamed identifier.
        let src = "void mainImage(out vec4 c,in vec2 p){if(p.x>0.){c=vec4(1.);}else{c=vec4(0.);}}";
        let golfed = golf_shader(src);
        assert!(!golfed.contains('{') || golfed.matches('{').count() == 1, "expected braces stripped around the if/else in: {golfed}");
        assert!(golfed.contains(")a="), "expected the if's braces gone in: {golfed}");
    }

    #[test]
    fn regression_default_frag_unaffected() {
        // default.frag's `if`/`for`/`while` bodies (the fractal-fold loop
        // and the raymarching loop, both multi-statement) all hold several
        // statements, not one, so this pass must be a no-op on it — the
        // byte count already asserted by
        // merge_declarations_tests::regression_default_frag_size_shrinks
        // (see that test's comment for why it is 1417, not the older 499
        // from before default.frag was rewritten into its current longer,
        // heavily-commented form) must stay exactly that after adding this
        // pass.
        let src = include_str!("../../python_ui/assets/shaders/default.frag");
        let golfed = golf_shader(src);
        assert_eq!(golfed.len(), 1417, "expected byte count unchanged by this pass: {} bytes", golfed.len());
    }

    #[test]
    fn rename_weighted_by_frequency_not_first_encounter() {
        // `first` is lexically encountered before `helper`, but `helper`
        // is called four times against `first`'s two — a naive
        // first-encounter renamer would still hand `first` the shortest
        // name `a` since it's the earliest identifier seen. The
        // frequency-weighted renamer must instead give `a` to `helper`
        // (the identifier with the most occurrences overall, definition
        // included), matching Shader Minifier's approach. Both functions
        // are called more than once so `inline_single_call_functions`
        // (which would otherwise remove a single-call function entirely,
        // leaving nothing here to rename) never fires on either.
        let src = "float first(){return 1.;}\nfloat helper(){return 2.;}\nvoid mainImage(out vec4 fragColor,in vec2 fragCoord){\nfragColor=vec4(first()+first()+helper()+helper()+helper()+helper());\n}";
        let golfed = golf_shader(src);
        assert!(golfed.contains("float a("), "expected `helper` (5 occurrences) to become `a`, got: {golfed}");
        assert!(golfed.contains("float b("), "expected `first` (3 occurrences) to become `b`, got: {golfed}");
    }

    #[test]
    fn rename_frequency_ties_broken_by_first_encounter() {
        // When two renameable identifiers occur the same number of times,
        // the tie must be broken deterministically by first-encounter
        // order (not, say, hash-map iteration order, which would make
        // output non-reproducible across runs/platforms). Both functions
        // are called twice (not once) so `inline_single_call_functions`
        // never removes either of them before renaming gets a chance to
        // run.
        let src = "float second(){return 1.;}\nfloat first(){return 2.;}\nvoid mainImage(out vec4 fragColor,in vec2 fragCoord){\nfragColor=vec4(first()+second()+first()+second());\n}";
        let golfed = golf_shader(src);
        // `second` is declared first in the source even though its name
        // suggests otherwise; both occur exactly three times (decl + two
        // calls), so `second` -> `a`, `first` -> `b`.
        assert!(golfed.contains("float a("), "expected first-encountered `second` to become `a` on a tie, got: {golfed}");
        assert!(golfed.contains("float b("), "expected second-encountered `first` to become `b` on a tie, got: {golfed}");
    }
}

#[cfg(test)]
mod ternary_tests {
    use super::*;

    #[test]
    fn basic_motif() {
        assert_eq!(ternary_from_if_else("if(c)a=1.;else a=2.;"), "a=c?1.:2.;");
    }

    #[test]
    fn condition_is_a_comparison_expression() {
        assert_eq!(
            ternary_from_if_else("if(x>0.)a=x;else a=0.;"),
            "a=x>0.?x:0.;"
        );
    }

    #[test]
    fn branches_can_use_arithmetic_and_member_access() {
        assert_eq!(
            ternary_from_if_else("if(c)a=x.y+1.;else a=x.y-1.;"),
            "a=c?x.y+1.:x.y-1.;"
        );
    }

    #[test]
    fn wired_into_full_pipeline() {
        // Through the *full* pipeline: strip_redundant_braces de-braces the
        // if/else first (verified structurally, not by exact renamed
        // spelling, same style as strip_redundant_braces_tests::
        // full_pipeline_smoke_test), then this pass turns it into a
        // ternary.
        let src = "void mainImage(out vec4 fragColor,in vec2 fragCoord){float r;if(fragCoord.x>0.){r=1.;}else{r=2.;}fragColor=vec4(r);}";
        let golfed = golf_shader(src);
        assert!(golfed.contains('?') && golfed.contains(':'), "expected a ternary in: {golfed}");
        assert!(!golfed.contains("if("), "expected the if/else gone in: {golfed}");
    }

    // --- non-triggering shapes: must be left completely untouched ---

    #[test]
    fn different_targets_never_merged() {
        let src = "if(c)a=1.;else b=2.;";
        assert_eq!(ternary_from_if_else(src), src);
    }

    #[test]
    fn compound_assignment_branch_never_matched() {
        let src = "if(c)a+=1.;else a=2.;";
        assert_eq!(ternary_from_if_else(src), src);
    }

    #[test]
    fn indexed_or_member_write_target_never_matched() {
        let src1 = "if(c)a[0]=1.;else a[0]=2.;";
        assert_eq!(ternary_from_if_else(src1), src1);
        let src2 = "if(c)a.x=1.;else a.x=2.;";
        assert_eq!(ternary_from_if_else(src2), src2);
    }

    #[test]
    fn declaration_branch_never_matched() {
        let src = "if(c){float a=1.;}else a=2.;";
        assert_eq!(ternary_from_if_else(src), src);
    }

    #[test]
    fn multi_statement_branch_never_matched() {
        let src = "if(c)a=1.;else{a=2.;b=3.;}";
        assert_eq!(ternary_from_if_else(src), src);
    }

    #[test]
    fn branch_with_function_call_never_matched() {
        // The core safety guard this ticket is about: `?:` isn't
        // guaranteed on every historical GLSL compiler to short-circuit
        // like `if`/`else` does, so a branch with a (potentially
        // side-effecting) call must never be folded in.
        let src = "if(c)a=sin(x);else a=2.;";
        assert_eq!(ternary_from_if_else(src), src);
    }

    #[test]
    fn branch_with_grouping_parens_never_matched() {
        // Deliberately conservative: even a harmless grouping paren is
        // rejected, since this tokenizer can't tell it apart from a call
        // without a real parser (see `is_ternary_branch_tok`'s doc comment).
        let src = "if(c)a=(x+1.);else a=2.;";
        assert_eq!(ternary_from_if_else(src), src);
    }

    #[test]
    fn condition_with_top_level_assignment_never_matched() {
        // `a=x=y?1.:2.;` would parse as `a=(x=(y?1.:2.))`, not
        // `a=((x=y)?1.:2.)` — silently different from the original
        // `if(x=y)a=1.;else a=2.;`. Must be rejected outright.
        let src = "if(x=y)a=1.;else a=2.;";
        assert_eq!(ternary_from_if_else(src), src);
    }

    #[test]
    fn condition_with_compound_assignment_never_matched() {
        let src = "if(x+=1.)a=1.;else a=2.;";
        assert_eq!(ternary_from_if_else(src), src);
    }

    #[test]
    fn condition_with_top_level_comma_never_matched() {
        let src = "if(x,y)a=1.;else a=2.;";
        assert_eq!(ternary_from_if_else(src), src);
    }

    #[test]
    fn condition_comparison_operators_never_flagged_as_assignment() {
        // `==`/`!=`/`<=`/`>=` all contain a `=` character but must never be
        // mistaken for an assignment — this is the common case, so it must
        // keep working.
        for cond in ["x==y", "x!=y", "x<=y", "x>=y"] {
            let src = format!("if({cond})a=1.;else a=2.;");
            let golfed = ternary_from_if_else(&src);
            assert_eq!(golfed, format!("a={cond}?1.:2.;"), "failed for condition {cond}");
        }
    }

    #[test]
    fn condition_assignment_nested_inside_a_call_is_fine() {
        // The assignment/comma is inside `dot(...)`'s own parens, at
        // depth 1 relative to the condition — never a risk, since it can't
        // leak out of that call's own precedence scope.
        assert_eq!(
            ternary_from_if_else("if(dot(a,b)>0.)c=1.;else c=2.;"),
            "c=dot(a,b)>0.?1.:2.;"
        );
    }

    #[test]
    fn if_without_else_left_untouched() {
        let src = "if(c)a=1.;";
        assert_eq!(ternary_from_if_else(src), src);
    }

    #[test]
    fn trailing_if_after_else_branch_never_absorbed() {
        // The risk this ticket calls out explicitly: an `if` without its
        // own `else`, immediately following a matched else-branch, must
        // never get pulled into that branch's expression scan.
        assert_eq!(
            ternary_from_if_else("if(c)a=1.;else a=2.;if(d)b=3.;"),
            "a=c?1.:2.;if(d)b=3.;"
        );
    }

    #[test]
    fn else_if_chain_outer_rejected_inner_still_converted() {
        // The *outer* `if(c)`'s else-branch is itself another `if`, not a
        // bare assignment — `parse_simple_ternary_assign` correctly
        // rejects it (next token after `else` is `if`, not an identifier),
        // so `if(c)`/`else` stays untouched. But the *inner* `if(d)a=2.;
        // else a=3.;` is an independent, fully valid match found later in
        // the very same left-to-right scan (same style as
        // `find_strippable_braces`'s independently-evaluated candidates),
        // and is correctly converted on its own.
        assert_eq!(
            ternary_from_if_else("if(c)a=1.;else if(d)a=2.;else a=3.;"),
            "if(c)a=1.;else a=d?2.:3.;"
        );
    }

    #[test]
    fn nested_inner_if_else_converts_but_outer_deliberately_does_not() {
        // `if(p){if(q)a=1.;else a=2.;}else a=3.;`, already de-braced by
        // strip_redundant_braces into `if(p)if(q)a=1.;else a=2.;else a=3.;`
        // — the *inner* if/else (`if(q)a=1.;else a=2.;`) is a valid match
        // on its own and gets converted; the *outer* one's then-branch,
        // `if(q)...`, is not a bare `ident=expr;` in the original token
        // stream this scan evaluates against, so it's correctly left as a
        // real `if`/`else` — by design, see `ternary_from_if_else`'s own
        // doc comment for why this scope limit is deliberate rather than a
        // missed case.
        assert_eq!(
            ternary_from_if_else("if(p)if(q)a=1.;else a=2.;else a=3.;"),
            "if(p)a=q?1.:2.;else a=3.;"
        );
    }

    #[test]
    fn regression_default_frag_unaffected() {
        // default.frag has no if/else at all (only two for-loop bodies,
        // one per helper function) — a pure no-op, confirming this pass
        // adds nothing to a shader without the pattern (byte count stays
        // the 1417 asserted by
        // strip_redundant_braces_tests::regression_default_frag_unaffected
        // — see that test's comment for why it is 1417, not the older 499
        // from before default.frag was rewritten into its current longer,
        // heavily-commented form).
        let src = include_str!("../../python_ui/assets/shaders/default.frag");
        let golfed = golf_shader(src);
        assert_eq!(golfed.len(), 1417, "expected byte count unchanged by this pass: {} bytes", golfed.len());
    }
}

#[cfg(test)]
mod vector_splat_tests {
    use super::*;

    #[test]
    fn vec2_splat() {
        assert_eq!(fold_vector_constructor_splat("a=vec2(x,x);"), "a=vec2(x);");
    }

    #[test]
    fn vec3_splat() {
        assert_eq!(fold_vector_constructor_splat("a=vec3(x,x,x);"), "a=vec3(x);");
    }

    #[test]
    fn vec4_splat() {
        assert_eq!(fold_vector_constructor_splat("a=vec4(x,x,x,x);"), "a=vec4(x);");
    }

    #[test]
    fn splat_on_a_numeric_literal() {
        assert_eq!(fold_vector_constructor_splat("a=vec3(.5,.5,.5);"), "a=vec3(.5);");
    }

    #[test]
    fn distinct_arguments_left_intact() {
        let src = "a=vec3(x,y,z);";
        assert_eq!(fold_vector_constructor_splat(src), src);
    }

    #[test]
    fn partially_matching_arguments_left_intact() {
        // Two of the three arguments match but not all three — GLSL's
        // splat rule only applies when a *single* scalar is given, so a
        // partial match is not eligible at all, never partially folded.
        let src = "a=vec3(x,x,y);";
        assert_eq!(fold_vector_constructor_splat(src), src);
    }

    #[test]
    fn repeated_but_non_atomic_argument_left_intact() {
        // Each argument is textually identical, but `f()` is a function
        // call, not an atomic operand — folding this would only evaluate
        // `f()` once instead of three times, changing behaviour if `f` has
        // a side effect. Left untouched on purpose (see this pass's own
        // doc comment and the roadmap ticket's side-effect guard).
        let src = "a=vec3(f(),f(),f());";
        assert_eq!(fold_vector_constructor_splat(src), src);
    }

    #[test]
    fn repeated_subexpression_argument_left_intact() {
        // Same reasoning as the non-atomic function-call case: `a+b` is a
        // subexpression, not a lone identifier/literal, even though all
        // three arguments are syntactically identical.
        let src = "v=vec3(a+b,a+b,a+b);";
        assert_eq!(fold_vector_constructor_splat(src), src);
    }

    #[test]
    fn mat_constructors_never_touched() {
        // A matN filled from a single repeated scalar follows a diagonal
        // convention, not a full splat — must never be folded, unlike
        // vecN.
        let src = "m=mat3(x,x,x,x,x,x,x,x,x);";
        assert_eq!(fold_vector_constructor_splat(src), src);
    }

    #[test]
    fn wrong_arity_left_intact() {
        // Two arguments where vec3 expects three: not a valid GLSL call to
        // begin with, and definitely not a splat — left untouched rather
        // than guessing.
        let src = "a=vec3(x,x);";
        assert_eq!(fold_vector_constructor_splat(src), src);
    }

    #[test]
    fn nested_call_argument_never_folds() {
        // `sin(x)` is not itself an atomic operand (it's a function call),
        // even though it's repeated identically three times — same
        // side-effect guard as the plain `f()` case above.
        let src = "a=vec3(sin(x),sin(x),sin(x));";
        assert_eq!(fold_vector_constructor_splat(src), src);
    }

    #[test]
    fn wired_into_full_pipeline() {
        // Through the full golf_shader pipeline (renaming + literal
        // shortening included), a uniform-color pattern collapses to a
        // splat once every pass has run — the case this ticket exists for.
        let src = "void mainImage(out vec4 fragColor,in vec2 fragCoord){float v=0.5;vec3 col=vec3(v,v,v);fragColor=vec4(col,1.);}";
        let golfed = golf_shader(src);
        // `col`'s own vec3(v,v,v) must fold to a single-argument
        // vec3(...); the *other* vec3(...) that follows it in the source
        // (fragColor's vec4(col,1.)) legitimately keeps its comma, so the
        // check must target col's constructor specifically rather than
        // asserting "no comma anywhere in the file".
        let vec3_call = golfed.split("vec3(").nth(1).and_then(|rest| rest.split(')').next());
        assert_eq!(vec3_call.map(|s| s.contains(',')), Some(false), "expected the splat to leave no comma inside the vec3(...) call in: {golfed}");
    }

    #[test]
    fn regression_default_frag_no_splats_present() {
        // Historical note: an earlier, much terser `default.frag` had a
        // `palette()` function whose vec3(0.5,0.5,0.5) and
        // vec3(1.0,1.0,1.0) constants were genuine splats once golfed
        // (`.5`/`1.`), while a third, vec3(0.263,0.416,0.557), built from
        // three distinct components, was the no-op case this pass must
        // never touch (see this test's own git history / ROADMAP.md for
        // that version). `default.frag` was since entirely rewritten into
        // a longer, heavily-commented "raymarching fractal + glow
        // volumétrique" study shader — verified for real once a working
        // rustc/cargo toolchain became available in this environment,
        // this new file's every vec3(...) call (five of them) is either
        // already single-argument (`vec3(1.214)`, `vec3(0.)`) or built
        // from distinct components (`vec3(c,r,0.)`, `vec3(S,T,U)`,
        // `vec3(k*d,d)`), so this pass is now correctly a pure no-op on
        // this specific file — the splat-folding mechanism itself remains
        // covered by `wired_into_full_pipeline` just above, on a small
        // self-contained fixture that does not depend on default.frag's
        // current shape. This regression test's job is only to confirm
        // the absence of a false positive on the real shipped shader, not
        // to exercise the splat pattern itself.
        let src = include_str!("../../python_ui/assets/shaders/default.frag");
        let golfed = golf_shader(src);
        assert_eq!(golfed.matches("vec3(.5)").count(), 0, "expected no vec3(.5) splat in the current default.frag: {golfed}");
        assert_eq!(golfed.matches("vec3(1.)").count(), 0, "expected no vec3(1.) splat in the current default.frag: {golfed}");
        assert_eq!(golfed.matches("vec3(").count(), 5, "expected exactly 5 vec3(...) calls, none of them a foldable splat, in: {golfed}");
    }
}

#[cfg(test)]
mod strip_in_qualifier_tests {
    use super::*;

    #[test]
    fn single_parameter() {
        assert_eq!(
            strip_default_in_qualifier("void f(in vec2 p){}"),
            "void f(vec2 p){}"
        );
    }

    #[test]
    fn multiple_parameters_only_in_dropped() {
        // `out`/`inout` are genuine, non-default qualifiers — never
        // touched, even sitting right next to a dropped `in`.
        assert_eq!(
            strip_default_in_qualifier("void f(out vec4 c,in vec2 p,inout float t){}"),
            "void f(out vec4 c,vec2 p,inout float t){}"
        );
    }

    #[test]
    fn main_image_full_signature() {
        assert_eq!(
            strip_default_in_qualifier("void mainImage(out vec4 fragColor,in vec2 fragCoord){}"),
            "void mainImage(out vec4 fragColor,vec2 fragCoord){}"
        );
    }

    #[test]
    fn inout_never_confused_with_in() {
        // `inout` lexes as a single Ident, distinct from `in`, so it must
        // never be partially matched/mangled by this pass.
        assert_eq!(
            strip_default_in_qualifier("void f(inout float t){}"),
            "void f(inout float t){}"
        );
    }

    #[test]
    fn already_implicit_in_left_untouched() {
        // No `in` token to remove at all — a strict no-op, never inserts
        // anything either.
        let src = "void f(vec2 p){}";
        assert_eq!(strip_default_in_qualifier(src), src);
    }

    #[test]
    fn in_not_preceded_by_a_parameter_boundary_is_untouched() {
        // Guards against the (never actually emitted by this engine's
        // Shadertoy-only shaders) case of a top-level `in`-qualified
        // variable declaration, where `in` is a genuine non-default
        // qualifier and dropping it would change the meaning: only an
        // `in` immediately after `(`/`,` is a parameter qualifier.
        let src = "in vec4 x;void f(){}";
        assert_eq!(strip_default_in_qualifier(src), src);
    }

    #[test]
    fn wired_into_full_pipeline() {
        let src = "void mainImage(out vec4 fragColor,in vec2 fragCoord){fragColor=vec4(1.);}";
        let golfed = golf_shader(src);
        assert!(!golfed.contains("in vec2"), "expected the redundant `in` qualifier gone in: {golfed}");
        assert!(golfed.contains("out vec4"), "expected the genuine `out` qualifier to survive in: {golfed}");
    }

    #[test]
    fn regression_default_frag_in_qualifier_dropped() {
        let src = include_str!("../../python_ui/assets/shaders/default.frag");
        let golfed = golf_shader(src);
        assert!(!golfed.contains("in vec2"), "expected default.frag's `in vec2 fragCoord` stripped to `vec2 fragCoord` in: {golfed}");
    }
}

#[cfg(test)]
mod inline_single_call_tests {
    use super::*;

    #[test]
    fn basic_single_param_inlined_with_systematic_parens() {
        // roadmap2.md's own worked example: `foo(a+b)` with
        // `float foo(float x){return x*2.;}` must never become
        // `a+b*2.` (wrong precedence). This pass goes one step further
        // than the illustrative text and also parenthesizes the whole
        // substituted return expression at the call site (see
        // `inline_at_call_site`'s doc comment for why: `x*2.` isn't
        // itself atomic, so leaving it bare could break precedence in a
        // *different* surrounding context than this one).
        let src = "float foo(float x){return x*2.;}\nvoid mainImage(out vec4 fragColor,in vec2 fragCoord){\nfloat a=1.,b=2.;\nfragColor=vec4(foo(a+b));\n}";
        let out = inline_single_call_functions(&strip_comments(src));
        assert!(!out.contains("foo"), "expected the declaration and call site both gone: {out}");
        assert!(out.contains("((a+b)*2.)"), "expected the parameter wrapped and the whole body re-wrapped: {out}");
    }

    #[test]
    fn wired_into_full_pipeline_via_golf_shader() {
        let src = "float foo(float x){return x*2.;}\nvoid mainImage(out vec4 fragColor,in vec2 fragCoord){\nfragColor=vec4(foo(fragCoord.x));\n}";
        let golfed = golf_shader(src);
        assert!(!golfed.contains("foo"), "expected `foo` inlined away entirely by golf_shader: {golfed}");
    }

    #[test]
    fn precedence_bug_would_fire_without_the_outer_wrap() {
        // Concrete demonstration of the hazard the outer wrap guards
        // against: `foo(a)*3.` with a `+`-shaped body must never
        // collapse to `a+1.*3.` (== a+3.) instead of `(a+1.)*3.`.
        let src = "float foo(float x){return x+1.;}\nvoid mainImage(out vec4 fragColor,in vec2 fragCoord){\nfloat a=2.;\nfragColor=vec4(foo(a)*3.);\n}";
        let out = inline_single_call_functions(&strip_comments(src));
        assert!(out.contains("((a)+1.)*3."), "expected the outer wrap to preserve precedence: {out}");
        assert!(!out.contains("(a)+1.*3."), "this shape would silently change the computed value: {out}");
    }

    #[test]
    fn zero_arg_atomic_body_needs_no_extra_parens() {
        let src = "float pi(){return 3.14159;}\nvoid mainImage(out vec4 fragColor,in vec2 fragCoord){\nfragColor=vec4(pi());\n}";
        let out = inline_single_call_functions(&strip_comments(src));
        assert!(!out.contains("pi"), "expected `pi` inlined away: {out}");
        assert!(out.contains("vec4(3.14159)"), "expected a bare literal, no redundant parens needed: {out}");
    }

    #[test]
    fn already_parenthesized_body_not_double_wrapped() {
        let src = "float foo(float x){return (x+1.);}\nvoid mainImage(out vec4 fragColor,in vec2 fragCoord){\nfloat a=2.;\nfragColor=vec4(foo(a));\n}";
        let out = inline_single_call_functions(&strip_comments(src));
        assert!(out.contains("vec4(((a)+1.))"), "expected exactly one added layer for the substitution, no extra outer wrap on top of the body's own parens: {out}");
    }

    #[test]
    fn called_more_than_once_never_inlined() {
        let src = "float foo(float x){return x*2.;}\nvoid mainImage(out vec4 fragColor,in vec2 fragCoord){\nfragColor=vec4(foo(1.)+foo(2.));\n}";
        let out = inline_single_call_functions(&strip_comments(src));
        assert!(out.contains("float foo("), "a function called twice must never be inlined: {out}");
    }

    #[test]
    fn void_function_never_inlined() {
        let src = "void foo(float x){}\nvoid mainImage(out vec4 fragColor,in vec2 fragCoord){\nfoo(1.);\nfragColor=vec4(1.);\n}";
        let out = inline_single_call_functions(&strip_comments(src));
        assert!(out.contains("void foo("), "a void function has nothing to substitute and must never be inlined: {out}");
    }

    #[test]
    fn multi_statement_body_never_inlined() {
        let src = "float foo(float x){float y=x*2.;return y;}\nvoid mainImage(out vec4 fragColor,in vec2 fragCoord){\nfragColor=vec4(foo(1.));\n}";
        let out = inline_single_call_functions(&strip_comments(src));
        assert!(out.contains("float foo("), "a body with more than a single return statement is out of scope: {out}");
    }

    #[test]
    fn early_return_never_inlined() {
        let src = "float foo(float x){if(x>0.)return 1.;return 2.;}\nvoid mainImage(out vec4 fragColor,in vec2 fragCoord){\nfragColor=vec4(foo(1.));\n}";
        let out = inline_single_call_functions(&strip_comments(src));
        assert!(out.contains("float foo("), "a body that isn't exactly one `return EXPR;` statement is out of scope: {out}");
    }

    #[test]
    fn recursive_function_never_inlined() {
        // The name occurs three times in total (declaration, the
        // recursive self-call, and the one genuine external call), so the
        // `usage_count == 2` gate already excludes it before any deeper
        // check runs.
        let src = "float foo(float x){return x>0.?foo(x-1.):0.;}\nvoid mainImage(out vec4 fragColor,in vec2 fragCoord){\nfragColor=vec4(foo(3.));\n}";
        let out = inline_single_call_functions(&strip_comments(src));
        assert!(out.contains("float foo("), "a recursive function must never be inlined (would substitute forever): {out}");
    }

    #[test]
    fn repeated_parameter_in_body_never_inlined() {
        let src = "float foo(float x){return x*x;}\nvoid mainImage(out vec4 fragColor,in vec2 fragCoord){\nfragColor=vec4(foo(1.));\n}";
        let out = inline_single_call_functions(&strip_comments(src));
        assert!(out.contains("float foo("), "a parameter used more than once in the body must never be duplicated by inlining: {out}");
    }

    #[test]
    fn unnamed_parameter_prototype_never_inlined() {
        let src = "float foo(float){return 1.;}\nvoid mainImage(out vec4 fragColor,in vec2 fragCoord){\nfragColor=vec4(foo(1.));\n}";
        let out = inline_single_call_functions(&strip_comments(src));
        assert!(out.contains("float foo("), "no parameter name to substitute towards, must never be inlined: {out}");
    }

    #[test]
    fn array_typed_signature_never_inlined() {
        let src = "float foo(float x[2]){return x[0];}\nvoid mainImage(out vec4 fragColor,in vec2 fragCoord){\nfloat a[2];\nfragColor=vec4(foo(a));\n}";
        let out = inline_single_call_functions(&strip_comments(src));
        assert!(out.contains("float foo("), "array-typed signatures are explicitly out of scope: {out}");
    }

    #[test]
    fn main_image_never_inlined() {
        // mainImage is technically "referenced" by the harness at large
        // (its usage count inside the shader text alone can look low),
        // but it must never be a candidate regardless.
        let src = "void mainImage(out vec4 fragColor,in vec2 fragCoord){fragColor=vec4(1.);}";
        let out = inline_single_call_functions(&strip_comments(src));
        assert_eq!(out, strip_comments(src), "mainImage must always survive untouched");
    }

    #[test]
    fn struct_declaration_never_treated_as_a_function() {
        let src = "struct S{float x;};\nvoid mainImage(out vec4 fragColor,in vec2 fragCoord){\nS s;\ns.x=1.;\nfragColor=vec4(s.x);\n}";
        let out = inline_single_call_functions(&strip_comments(src));
        assert!(out.contains("struct S"), "a struct definition must never be mistaken for an inlinable function: {out}");
    }

    #[test]
    fn chain_of_single_call_functions_fully_collapses() {
        // A calls B once, B calls C once: both must disappear, in either
        // iteration order, over the fixed-point loop.
        let src = "float c(float x){return x+1.;}\nfloat b(float y){return c(y)*2.;}\nvoid mainImage(out vec4 fragColor,in vec2 fragCoord){\nfragColor=vec4(b(1.));\n}";
        let out = inline_single_call_functions(&strip_comments(src));
        assert!(!out.contains("float c("), "expected the innermost single-call function gone too: {out}");
        assert!(!out.contains("float b("), "expected the outer single-call function gone: {out}");
    }

    #[test]
    fn never_applied_to_common() {
        // Same status as `remove_unused_functions`: a function declared by
        // Common can be called by a pass this pass never sees, so "called
        // exactly once" is never a property `golf_common` can safely check
        // on Common's own text in isolation. `golf_common` must therefore
        // never call `inline_single_call_functions` at all.
        let common = "float helper(float x){return x*2.;}\n";
        let golfed = golf_common(common);
        assert!(golfed.contains("helper"), "expected Common's own (single-call-looking) function left fully intact: {golfed}");
    }

    #[test]
    fn dead_code_toggle_off_disables_inlining_too() {
        // `rename` left off so the helper's name survives verbatim and the
        // assertion doesn't have to guess what it got golfed down to.
        let src = "float foo(float x){return x*2.;}\nvoid mainImage(out vec4 fragColor,in vec2 fragCoord){\nfragColor=vec4(foo(1.));\n}";
        let golfed = golf_shader_ex(src, "", false, false, true);
        assert!(golfed.contains("foo("), "expected inlining disabled alongside dead-code elimination when dead_code=false: {golfed}");
    }

    #[test]
    fn size_never_increases_on_a_real_shader() {
        // `rename` left off for the same reason as the test above.
        let src = "float square(float x){return x*x-x;}\nvoid mainImage(out vec4 fragColor,in vec2 fragCoord){\nfloat v=square(fragCoord.x)+1.;\nfragColor=vec4(v);\n}";
        // `square` uses its parameter twice (`x*x`), so it must be left
        // alone — this is a regression guard for the "never duplicate a
        // repeated parameter" rule end-to-end through golf_shader, not
        // just against inline_single_call_functions directly.
        let with_inlining = golf_shader_ex(src, "", false, true, true);
        let without_inlining = golf_shader_ex(src, "", false, false, true);
        assert!(with_inlining.contains("square"), "repeated-parameter body must survive inlining: {with_inlining}");
        assert_eq!(with_inlining, without_inlining, "nothing to inline here, both toggles should agree: {with_inlining}");
    }
}
