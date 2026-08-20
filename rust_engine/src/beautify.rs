//! "Dé-golf": reformats GLSL source into readable, indented code —
//! functionally the opposite direction of `golf.rs`, though it is not
//! actually paired with it (golfing that renamed identifiers/inlined
//! functions/dropped dead code cannot be undone by a formatter alone; this
//! only restores *layout*, never lost names or structure). Most useful on
//! already-golfed or otherwise cramped code — pasted-in Shadertoy minified
//! shaders, code golfed by this app itself, or just messily hand-formatted
//! code — to make it readable again before editing it further.
//!
//! `beautify_shader` never changes the token stream itself, only the
//! whitespace between tokens: every identifier, number, operator,
//! directive and comment from the input reappears in the output in the
//! same order, character-for-character. GLSL (like C) is whitespace-
//! insensitive outside of comments/preprocessor-directive line endings, so
//! that guarantee alone means this transform can never change what the
//! shader compiles to — the one place it takes active care is *not*
//! silently fusing two adjacent single-character operators (e.g. two
//! unary `-` in `- -x`) into a different multi-character token by
//! dropping the space between them; see `is_dangerous_operator_pair`
//! (shared with `golf.rs`, which faces the exact same hazard the other
//! way around when *removing* whitespace).
//!
//! The formatting rules themselves are a heuristic, not a real GLSL
//! parser: brace/paren nesting drives indentation and where lines break,
//! and a small set of token-adjacency rules decides where a space goes
//! (e.g. telling a unary `-x` from a binary `a - b` by looking only at
//! the token immediately before the `-`). This covers the vast majority
//! of real shader code correctly; a pathological expression might come
//! out slightly oddly spaced, but — per the guarantee above — never
//! incorrectly.

const INDENT_UNIT: &str = "    ";

#[derive(Debug, Clone, PartialEq)]
enum BTok {
    Ident(String),
    Number(String),
    Punct(String),
    LineComment(String),
    BlockComment(String),
    Directive(String),
}

#[derive(Debug, Clone)]
struct Token {
    tok: BTok,
    /// At least one newline separated this token from the previous one in
    /// the source (false for the very first token).
    newline_before: bool,
    /// Two or more newlines did (a genuine blank line the user left on
    /// purpose) — preserved as a single blank output line.
    blank_before: bool,
}

// Longest-match-first: `<<=`/`>>=` must be tried before `<<`/`>>`, which
// must be tried before the bare `<`/`>` single-char fallback.
const MULTICHAR_OPS: &[&str] = &[
    "<<=", ">>=", "==", "!=", "<=", ">=", "&&", "||", "++", "--", "+=", "-=", "*=", "/=", "%=",
    "&=", "|=", "^=", "<<", ">>",
];

fn match_multichar_operator(chars: &[char], i: usize) -> Option<&'static str> {
    for op in MULTICHAR_OPS {
        let opc: Vec<char> = op.chars().collect();
        if i + opc.len() <= chars.len() && chars[i..i + opc.len()] == opc[..] {
            return Some(op);
        }
    }
    None
}

fn tokenize(src: &str) -> Vec<Token> {
    let chars: Vec<char> = src.chars().collect();
    let n = chars.len();
    let mut i = 0;
    let mut out = Vec::new();
    let mut newline_before = false;
    let mut blank_before = false;
    let mut first = true;

    let push = |out: &mut Vec<Token>, tok: BTok, nb: &mut bool, bb: &mut bool, first: &mut bool| {
        out.push(Token { tok, newline_before: !*first && *nb, blank_before: !*first && *bb });
        *nb = false;
        *bb = false;
        *first = false;
    };

    while i < n {
        let c = chars[i];
        if c.is_whitespace() {
            let mut newlines = 0u32;
            while i < n && chars[i].is_whitespace() {
                if chars[i] == '\n' {
                    newlines += 1;
                }
                i += 1;
            }
            if newlines >= 1 {
                newline_before = true;
            }
            if newlines >= 2 {
                blank_before = true;
            }
            continue;
        }
        if c == '/' && i + 1 < n && chars[i + 1] == '/' {
            let start = i;
            while i < n && chars[i] != '\n' {
                i += 1;
            }
            let text: String = chars[start..i].iter().collect();
            push(&mut out, BTok::LineComment(text), &mut newline_before, &mut blank_before, &mut first);
            continue;
        }
        if c == '/' && i + 1 < n && chars[i + 1] == '*' {
            let start = i;
            i += 2;
            while i + 1 < n && !(chars[i] == '*' && chars[i + 1] == '/') {
                i += 1;
            }
            i = (i + 2).min(n);
            let text: String = chars[start..i].iter().collect();
            push(&mut out, BTok::BlockComment(text), &mut newline_before, &mut blank_before, &mut first);
            continue;
        }
        if c == '#' {
            // A preprocessor directive always runs to the end of its
            // (possibly `\`-continued) physical line -- scanned as one
            // opaque token so it can never be reflowed or merged with
            // whatever comes next, the one part of this format where line
            // placement is not cosmetic.
            let start = i;
            i += 1;
            loop {
                while i < n && chars[i] != '\n' && chars[i] != '\\' {
                    i += 1;
                }
                if i < n && chars[i] == '\\' && i + 1 < n && chars[i + 1] == '\n' {
                    i += 2;
                    continue;
                }
                if i < n && chars[i] == '\\' {
                    i += 1;
                    continue;
                }
                break;
            }
            let text: String = chars[start..i].iter().collect();
            push(&mut out, BTok::Directive(text), &mut newline_before, &mut blank_before, &mut first);
            continue;
        }
        if crate::golf::is_ident_start(c) {
            let start = i;
            while i < n && crate::golf::is_ident_char(chars[i]) {
                i += 1;
            }
            let text: String = chars[start..i].iter().collect();
            push(&mut out, BTok::Ident(text), &mut newline_before, &mut blank_before, &mut first);
            continue;
        }
        if c.is_ascii_digit() || (c == '.' && i + 1 < n && chars[i + 1].is_ascii_digit()) {
            let start = i;
            if let Some(end) = crate::golf::try_scan_float_literal(&chars, i) {
                i = end;
            } else {
                while i < n && chars[i].is_ascii_digit() {
                    i += 1;
                }
            }
            // trailing literal suffix (u/U for uint, f/F/lf/LF for float)
            while i < n && chars[i].is_ascii_alphabetic() {
                i += 1;
            }
            let text: String = chars[start..i].iter().collect();
            push(&mut out, BTok::Number(text), &mut newline_before, &mut blank_before, &mut first);
            continue;
        }
        if let Some(op) = match_multichar_operator(&chars, i) {
            push(&mut out, BTok::Punct(op.to_string()), &mut newline_before, &mut blank_before, &mut first);
            i += op.chars().count();
            continue;
        }
        push(&mut out, BTok::Punct(c.to_string()), &mut newline_before, &mut blank_before, &mut first);
        i += 1;
    }
    out
}

const BINARY_OPS: &[&str] = &[
    "=", "==", "!=", "<", ">", "<=", ">=", "&&", "||", "&", "|", "^", "<<", ">>", "+", "-", "*",
    "/", "%", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=", "?",
];
const NO_SPACE_BEFORE: &[&str] = &[")", "]", ";", ",", ".", ":"];
const NO_SPACE_AFTER: &[&str] = &["(", "[", "."];
const KEYWORDS_SPACE_BEFORE_PAREN: &[&str] = &["if", "for", "while", "switch", "return"];
const NON_OPERAND_KEYWORDS: &[&str] = &["return", "case"];

fn punct<'a>(t: &'a BTok) -> Option<&'a str> {
    if let BTok::Punct(s) = t {
        Some(s.as_str())
    } else {
        None
    }
}

/// Whether `t` could be the last token of a complete operand (so a `+`/`-`
/// immediately after it is binary, not unary) -- the one piece of lookback
/// this formatter needs, kept deliberately narrow (see module docs).
fn is_operand_end(t: &BTok) -> bool {
    match t {
        BTok::Number(_) => true,
        BTok::Ident(w) => !NON_OPERAND_KEYWORDS.contains(&w.as_str()),
        BTok::Punct(p) => p == ")" || p == "]" || p == "++" || p == "--",
        _ => false,
    }
}

fn first_char(t: &BTok) -> Option<char> {
    match t {
        BTok::Ident(s) | BTok::Number(s) | BTok::Punct(s) => s.chars().next(),
        _ => None,
    }
}

fn last_char(t: &BTok) -> Option<char> {
    match t {
        BTok::Ident(s) | BTok::Number(s) | BTok::Punct(s) => s.chars().last(),
        _ => None,
    }
}

/// Decides whether a space belongs between `prev` and `cur` when both land
/// on the same output line. `prev_is_unary_prefix` is `true` when `prev`
/// is a `+`/`-`/`!`/`~` this same function already classified as a unary
/// operator (via the `is_operand_end` check on *its* own predecessor) --
/// tracked by the caller across calls, since that classification depends
/// on a token this function no longer has in scope.
fn needs_space(prev: &BTok, prev_is_unary_prefix: bool, cur: &BTok) -> bool {
    if prev_is_unary_prefix {
        // Almost always tight ("-x", "!flag", "++i") -- except when that
        // would silently fuse two adjacent operator characters into a
        // different, longer token than the source actually had (`- -x`
        // must never become `--x`). Mirrors golf.rs's own guard against
        // the same hazard in the opposite direction.
        if let (Some(pc), Some(cc)) = (last_char(prev), first_char(cur)) {
            if crate::golf::is_dangerous_operator_pair(pc, cc) {
                return true;
            }
        }
        return false;
    }
    if let Some(p) = punct(cur) {
        if NO_SPACE_BEFORE.contains(&p) {
            return false;
        }
    }
    if let Some(p) = punct(prev) {
        if NO_SPACE_AFTER.contains(&p) {
            return false;
        }
    }
    if let Some(p) = punct(cur) {
        if (p == "++" || p == "--") && is_operand_end(prev) {
            return false; // postfix: glue to the operand before it
        }
        if p == "(" {
            return match prev {
                BTok::Ident(w) => KEYWORDS_SPACE_BEFORE_PAREN.contains(&w.as_str()),
                _ => !is_operand_end(prev),
            };
        }
        if p == "[" {
            return false; // indexing/array-size: always tight
        }
        if p == "{" {
            return true;
        }
        if BINARY_OPS.contains(&p) {
            return true;
        }
    }
    if let Some(p) = punct(prev) {
        if BINARY_OPS.contains(&p) || p == "," || p == ";" || p == ":" {
            return true;
        }
    }
    if is_operand_end(prev) && matches!(cur, BTok::Ident(_) | BTok::Number(_)) {
        return true; // e.g. the type/name boundary in "vec3 p"
    }
    true // conservative default: an extra space never changes meaning
}

/// Reformats `src`: reindents by brace depth, puts most statements on
/// their own line (but never splits a `for (...;...;...)` header, and
/// keeps `} else {` together), and re-spaces operators using
/// `needs_space`. See the module docs for the exact "never changes what
/// this compiles to" guarantee and its one caveat.
pub fn beautify_shader(src: &str) -> String {
    let tokens = tokenize(src);
    let mut out = String::with_capacity(src.len() * 2);
    let mut indent: i32 = 0;
    let mut paren_depth: i32 = 0;
    let mut at_line_start = true;
    let mut prev: Option<BTok> = None;
    let mut prev_is_unary_prefix = false;
    let mut pending_break = false;

    let mut i = 0;
    while i < tokens.len() {
        let Token { tok, newline_before, blank_before } = tokens[i].clone();

        // A break was requested by the previous token (end of a
        // statement/block), unless this token is a comment that sat on
        // that same source line (a trailing `foo(); // note` comment) --
        // in which case it's placed first, and the break happens after it
        // instead, further down.
        let is_trailing_comment_here =
            pending_break && !newline_before && matches!(tok, BTok::LineComment(_) | BTok::BlockComment(_));
        if pending_break && !is_trailing_comment_here {
            if !at_line_start {
                out.push('\n');
                at_line_start = true;
            }
            pending_break = false;
        }

        if matches!(tok, BTok::Punct(ref p) if p == "}") {
            if !at_line_start {
                out.push('\n');
                at_line_start = true;
            }
            indent = (indent - 1).max(0);
        }

        if at_line_start && prev.is_some() && blank_before {
            out.push('\n');
        }

        if at_line_start {
            for _ in 0..indent {
                out.push_str(INDENT_UNIT);
            }
        } else if let Some(p) = &prev {
            if needs_space(p, prev_is_unary_prefix, &tok) {
                out.push(' ');
            }
        }
        at_line_start = false;

        match &tok {
            BTok::Ident(s) | BTok::Number(s) | BTok::Punct(s) => out.push_str(s),
            BTok::LineComment(s) | BTok::BlockComment(s) | BTok::Directive(s) => out.push_str(s),
        }

        // Classify *this* token, if it's a candidate unary prefix, using
        // the token that preceded it -- needed by `needs_space` on the
        // next iteration.
        prev_is_unary_prefix = match &tok {
            BTok::Punct(p) if p == "!" || p == "~" => true,
            BTok::Punct(p) if p == "+" || p == "-" => {
                !prev.as_ref().map(is_operand_end).unwrap_or(false)
            }
            _ => false,
        };

        match &tok {
            BTok::Punct(p) if p == "(" || p == "[" => paren_depth += 1,
            BTok::Punct(p) if p == ")" || p == "]" => paren_depth -= 1,
            BTok::Punct(p) if p == "{" => {
                indent += 1;
                out.push('\n');
                at_line_start = true;
                pending_break = false;
            }
            BTok::Punct(p) if p == "}" => {
                let stays_for_else =
                    matches!(tokens.get(i + 1), Some(t) if matches!(&t.tok, BTok::Ident(w) if w == "else"));
                if !stays_for_else {
                    pending_break = true;
                }
            }
            BTok::Punct(p) if p == ";" => {
                if paren_depth <= 0 {
                    pending_break = true;
                }
            }
            BTok::LineComment(_) => {
                out.push('\n');
                at_line_start = true;
                pending_break = false;
            }
            BTok::Directive(_) => {
                out.push('\n');
                at_line_start = true;
                pending_break = false;
            }
            BTok::BlockComment(_) => {
                // Force a break after a block comment that either closed
                // a deferred statement-end break, or sat on its own
                // source line (so the next token started a new line too)
                // -- but leave a truly inline one (`a = /* n */ b;`)
                // exactly where it was, mid-line.
                let next_starts_new_line =
                    tokens.get(i + 1).map(|t| t.newline_before).unwrap_or(false);
                if is_trailing_comment_here || next_starts_new_line {
                    out.push('\n');
                    at_line_start = true;
                    pending_break = false;
                }
            }
            _ => {}
        }

        prev = Some(tok);
        i += 1;
    }

    let mut result = out.trim_end().to_string();
    result.push('\n');
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn indents_by_brace_depth() {
        let out = beautify_shader("void mainImage(out vec4 c,in vec2 f){if(f.x>0.){c=vec4(1.);}}");
        assert_eq!(
            out,
            "void mainImage(out vec4 c, in vec2 f) {\n    if (f.x > 0.) {\n        c = vec4(1.);\n    }\n}\n"
        );
    }

    #[test]
    fn keeps_for_loop_header_on_one_line() {
        let out = beautify_shader("void f(){for(int i=0;i<10;i++){x+=1.;}}");
        assert!(out.contains("for (int i = 0; i < 10; i++) {\n"), "{out}");
    }

    #[test]
    fn distinguishes_unary_from_binary_minus() {
        let out = beautify_shader("float a=-1.;float b=a-2.;");
        assert!(out.contains("a = -1.;"), "{out}");
        assert!(out.contains("b = a - 2.;"), "{out}");
    }

    #[test]
    fn never_fuses_double_unary_minus_into_decrement() {
        let out = beautify_shader("float a=- -1.;");
        assert!(!out.contains("--1."), "must not turn '- -1.' into '--1.': {out}");
    }

    #[test]
    fn keeps_closing_brace_and_else_together() {
        let out = beautify_shader("void f(){if(true){a=1.;}else{a=2.;}}");
        assert!(out.contains("} else {"), "{out}");
    }

    #[test]
    fn preserves_directives_and_line_comments() {
        let out = beautify_shader("#define PI 3.14159\nfloat f(){return PI;// a comment\n}");
        assert!(out.starts_with("#define PI 3.14159\n"), "{out}");
        assert!(out.contains("// a comment"), "{out}");
    }

    #[test]
    fn round_trip_is_stable() {
        let once = beautify_shader("void f(){float a=1.;float b=2.;a=a+b;}");
        let twice = beautify_shader(&once);
        assert_eq!(once, twice, "beautifying already-beautified code must be a no-op");
    }
}
