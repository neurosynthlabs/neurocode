"""Reading source code by its syntax tree, for every language the bundled grammars cover.

The code index used to read everything but Python and T-SQL by patterns: a regular expression per
declaration shape, which knows nothing of comments, strings or nesting. This reads each file with a
real parser — tree-sitter, through `tree-sitter-language-pack`, whose grammars ship inside the wheel,
so nothing is ever downloaded and the product indexes offline — and asks the tree small questions:
what is declared here and where it ends, what this file imports, what it calls, which type names it
uses, and how many branches it holds.

The pack carries no tags queries of its own, so the questions are written here, one small query per
language. Each query is compiled once per process and only for a language the repository has: a
grammar costs nothing until the first file of its language turns up.

Nothing here knows about the database or about other files. `codeindex` turns an `Outline` into
symbols and edges; this module only reads one file.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from functools import cache

from tree_sitter import Language, Node, Parser, Query, QueryCursor, QueryError
from tree_sitter_language_pack import get_language, get_parser


@dataclass
class Outline:
    """What one file declares and reaches, as its syntax tree says."""

    #: name, kind, first line, exported, last line
    symbols: list[tuple[str, str, int, bool, int]] = field(default_factory=list)
    #: (how, specifier): how is "" for the language's own import form, "relative" for a path next to
    #: the file (require_relative, source, include, #include "x"), "system" for <x>, "mod" for Rust's
    #: `mod x;`, "package" for a library named by name (R's library(), Julia's using)
    imports: list[tuple[str, str]] = field(default_factory=list)
    #: the names it calls, and the type or module names it mentions
    refs: set[str] = field(default_factory=set)
    #: the package, namespace or module this file declares itself part of
    package: str = ""
    branches: int = 0


# ── the questions, per language ──────────────────────────────────
# Captures: @def.<kind> on a declaration with its @name; @owner names the type a method belongs to
# when the tree does not nest it (Go receivers, Lua's M.add); @scope is a container that is not a
# symbol itself (Rust's impl, Swift's extension); @shape refines a kind (a Go type spec is a struct
# or an interface by what it is). Everything else is a flat capture: @import.<how>, @call, @ref,
# @package. Captures that start with an underscore only feed a predicate.

_TS_COMMON = """
(function_declaration name: (_) @name) @def.function
(generator_function_declaration name: (_) @name) @def.function
(class_declaration name: (_) @name) @def.class
(method_definition name: (_) @name) @def.function
(variable_declarator name: (identifier) @name value: [(arrow_function) (function_expression)]) @def.function
(export_statement declaration: (lexical_declaration (variable_declarator name: (identifier) @name) @def.constant))
"""
_TS_ONLY = """
(abstract_class_declaration name: (_) @name) @def.class
(interface_declaration name: (_) @name) @def.interface
(type_alias_declaration name: (_) @name) @def.type
(enum_declaration name: (_) @name) @def.enum
"""
_TS_FLAT = """
(import_statement source: (string (string_fragment) @import))
(export_statement source: (string (string_fragment) @import))
(call_expression function: (identifier) @_f arguments: (arguments . (string (string_fragment) @import)) (#eq? @_f "require"))
(call_expression function: (import) arguments: (arguments . (string (string_fragment) @import)))
(call_expression function: (identifier) @call)
(call_expression function: (member_expression property: (property_identifier) @call))
(new_expression constructor: (identifier) @ref)
"""

DEFS: dict[str, str] = {
    "typescript": _TS_COMMON + _TS_ONLY,
    "tsx": _TS_COMMON + _TS_ONLY,
    "javascript": _TS_COMMON,
    "go": """
(function_declaration name: (identifier) @name) @def.function
(method_declaration receiver: (parameter_list (parameter_declaration type: [
  (type_identifier) @owner (pointer_type (type_identifier) @owner)
  (generic_type type: (type_identifier) @owner) (pointer_type (generic_type type: (type_identifier) @owner))]))
  name: (field_identifier) @name) @def.method
(type_spec name: (type_identifier) @name type: (_) @shape) @def.type
(source_file (const_declaration (const_spec name: (identifier) @name) @def.constant))
""",
    "rust": """
(function_item name: (identifier) @name) @def.function
(function_signature_item name: (identifier) @name) @def.function
(struct_item name: (type_identifier) @name) @def.struct
(enum_item name: (type_identifier) @name) @def.enum
(union_item name: (type_identifier) @name) @def.struct
(trait_item name: (type_identifier) @name) @def.trait
(type_item name: (type_identifier) @name) @def.type
(const_item name: (identifier) @name) @def.constant
(static_item name: (identifier) @name) @def.constant
(mod_item name: (identifier) @name body: (declaration_list)) @def.module
(impl_item type: [(type_identifier) @name (generic_type type: (type_identifier) @name)
                  (scoped_type_identifier name: (type_identifier) @name)]) @scope
""",
    "java": """
(class_declaration name: (identifier) @name) @def.class
(interface_declaration name: (identifier) @name) @def.interface
(enum_declaration name: (identifier) @name) @def.enum
(record_declaration name: (identifier) @name) @def.class
(annotation_type_declaration name: (identifier) @name) @def.interface
(method_declaration name: (identifier) @name) @def.function
""",
    "kotlin": """
(class_declaration (type_identifier) @name) @def.class
(object_declaration (type_identifier) @name) @def.object
(function_declaration (simple_identifier) @name) @def.function
""",
    "csharp": """
(class_declaration name: (identifier) @name) @def.class
(interface_declaration name: (identifier) @name) @def.interface
(enum_declaration name: (identifier) @name) @def.enum
(struct_declaration name: (identifier) @name) @def.struct
(record_declaration name: (identifier) @name) @def.class
(method_declaration name: (identifier) @name) @def.function
""",
    "c": """
(function_definition declarator: (function_declarator declarator: (identifier) @name)) @def.function
(function_definition declarator: (pointer_declarator declarator: (function_declarator declarator: (identifier) @name))) @def.function
(struct_specifier name: (type_identifier) @name body: (field_declaration_list)) @def.struct
(union_specifier name: (type_identifier) @name body: (field_declaration_list)) @def.struct
(enum_specifier name: (type_identifier) @name body: (enumerator_list)) @def.enum
(type_definition declarator: (type_identifier) @name) @def.type
""",
    "cpp": """
(function_definition declarator: (function_declarator declarator: [(identifier) (field_identifier) (qualified_identifier)] @name)) @def.function
(function_definition declarator: (pointer_declarator declarator: (function_declarator declarator: [(identifier) (qualified_identifier)] @name))) @def.function
(function_definition declarator: (reference_declarator (function_declarator declarator: [(identifier) (qualified_identifier)] @name))) @def.function
(class_specifier name: (type_identifier) @name body: (field_declaration_list)) @def.class
(struct_specifier name: (type_identifier) @name body: (field_declaration_list)) @def.struct
(union_specifier name: (type_identifier) @name body: (field_declaration_list)) @def.struct
(enum_specifier name: (type_identifier) @name body: (enumerator_list)) @def.enum
(type_definition declarator: (type_identifier) @name) @def.type
(alias_declaration name: (type_identifier) @name) @def.type
""",
    "ruby": """
(class name: (_) @name) @def.class
(module name: (_) @name) @def.module
(method name: (_) @name) @def.function
(singleton_method name: (_) @name) @def.function
""",
    "php": """
(class_declaration name: (name) @name) @def.class
(interface_declaration name: (name) @name) @def.interface
(trait_declaration name: (name) @name) @def.trait
(enum_declaration name: (name) @name) @def.enum
(function_definition name: (name) @name) @def.function
(method_declaration name: (name) @name) @def.function
""",
    "swift": """
(class_declaration declaration_kind: ["class" "struct" "enum" "actor"] @shape name: (type_identifier) @name) @def.class
(class_declaration declaration_kind: "extension" name: (user_type (type_identifier) @name)) @scope
(protocol_declaration name: (type_identifier) @name) @def.interface
(function_declaration name: (simple_identifier) @name) @def.function
(protocol_function_declaration name: (simple_identifier) @name) @def.function
""",
    "scala": """
(class_definition name: (identifier) @name) @def.class
(object_definition name: (identifier) @name) @def.object
(trait_definition name: (identifier) @name) @def.trait
(enum_definition name: (identifier) @name) @def.enum
(function_definition name: (identifier) @name) @def.function
(function_declaration name: (identifier) @name) @def.function
""",
    "dart": """
(class_definition name: (identifier) @name) @def.class
(enum_declaration name: (identifier) @name) @def.enum
(mixin_declaration (identifier) @name) @def.class
(extension_declaration name: (identifier) @name) @scope
(program (function_signature name: (identifier) @name) @def.function)
(method_signature (function_signature name: (identifier) @name)) @def.function
""",
    "lua": """
(function_declaration name: (identifier) @name) @def.function
(function_declaration name: (dot_index_expression table: (identifier) @owner field: (identifier) @name)) @def.function
(function_declaration name: (method_index_expression table: (identifier) @owner method: (identifier) @name)) @def.method
""",
    "r": """
(program (binary_operator lhs: (identifier) @name rhs: (function_definition)) @def.function)
""",
    "julia": """
(function_definition (signature (call_expression . (identifier) @name))) @def.function
(source_file (assignment . (call_expression . (identifier) @name)) @def.function)
(module_definition (block (assignment . (call_expression . (identifier) @name)) @def.function))
(struct_definition (type_head (identifier) @name)) @def.struct
(struct_definition (type_head (binary_expression . (identifier) @name))) @def.struct
(abstract_definition (type_head (identifier) @name)) @def.type
(module_definition name: (identifier) @name) @def.module
""",
    "elixir": """
(call target: (identifier) @_k (arguments (alias) @name) (#eq? @_k "defmodule")) @def.module
(call target: (identifier) @_k (arguments [(identifier) @name (call target: (identifier) @name)
  (binary_operator left: (call target: (identifier) @name))])
  (#any-of? @_k "def" "defp" "defmacro" "defmacrop")) @def.function
(call target: (identifier) @_k (arguments (alias) @name) (#eq? @_k "defprotocol")) @def.interface
""",
    "haskell": """
(declarations (function name: (variable) @name) @def.function)
(declarations (bind name: (variable) @name) @def.function)
(data_type name: (name) @name) @def.type
(newtype name: (name) @name) @def.type
(type_synomym name: (name) @name) @def.type
(class name: (name) @name) @def.interface
""",
    "ocaml": """
(value_definition (let_binding pattern: (value_name) @name) @def.function)
(type_definition (type_binding name: (type_constructor) @name) @def.type)
(module_definition (module_binding (module_name) @name) @def.module)
""",
    "zig": """
(Decl (FnProto function: (IDENTIFIER) @name)) @def.function
(Decl (VarDecl variable_type_function: (IDENTIFIER) @name (ErrorUnionExpr (SuffixExpr (ContainerDecl) @shape)))) @def.struct
(source_file (Decl (VarDecl variable_type_function: (IDENTIFIER) @name)) @def.constant)
""",
    "bash": """
(function_definition name: (word) @name) @def.function
""",
    "powershell": """
(function_statement (function_name) @name) @def.function
""",
}

FLAT: dict[str, str] = {
    "typescript": _TS_FLAT + "(type_identifier) @ref\n",
    "tsx": _TS_FLAT + "(type_identifier) @ref\n",
    "javascript": _TS_FLAT,
    "go": """
(import_spec path: (interpreted_string_literal (interpreted_string_literal_content) @import))
(package_clause (package_identifier) @package)
(call_expression function: (identifier) @call)
(call_expression function: (selector_expression field: (field_identifier) @call))
(type_identifier) @ref
""",
    "rust": """
(use_declaration argument: (_) @import)
(mod_item name: (identifier) @import.mod !body)
(call_expression function: (identifier) @call)
(call_expression function: (scoped_identifier name: (identifier) @call))
(call_expression function: (field_expression field: (field_identifier) @call))
(type_identifier) @ref
""",
    "java": """
(package_declaration (_) @package)
(import_declaration) @import.statement
(method_invocation name: (identifier) @call)
(method_invocation object: (identifier) @ref)
(type_identifier) @ref
""",
    "kotlin": """
(package_header (identifier) @package)
(import_header) @import.statement
(call_expression (simple_identifier) @call)
(call_expression (navigation_expression (simple_identifier) @ref))
(user_type (type_identifier) @ref)
""",
    "csharp": """
(namespace_declaration name: (_) @package)
(file_scoped_namespace_declaration name: (_) @package)
(using_directive [(identifier) (qualified_name)] @import)
(invocation_expression function: (identifier) @call)
(invocation_expression function: (member_access_expression name: (identifier) @call))
(member_access_expression expression: (identifier) @ref)
(object_creation_expression type: (identifier) @ref)
(variable_declaration type: (identifier) @ref)
(parameter type: (identifier) @ref)
(base_list (identifier) @ref)
(type_argument_list (identifier) @ref)
(property_declaration type: (identifier) @ref)
(method_declaration returns: (identifier) @ref)
""",
    "c": """
(preproc_include path: (string_literal (string_content) @import.relative))
(preproc_include path: (system_lib_string) @import.system)
(call_expression function: (identifier) @call)
(call_expression function: (field_expression field: (field_identifier) @call))
(type_identifier) @ref
""",
    "cpp": """
(preproc_include path: (string_literal (string_content) @import.relative))
(preproc_include path: (system_lib_string) @import.system)
(call_expression function: (identifier) @call)
(call_expression function: (field_expression field: (field_identifier) @call))
(call_expression function: (qualified_identifier name: (identifier) @call))
(type_identifier) @ref
""",
    "ruby": """
(call method: (identifier) @_m arguments: (argument_list . (string (string_content) @import))
  (#any-of? @_m "require" "load"))
(call method: (identifier) @_m arguments: (argument_list . (string (string_content) @import.relative))
  (#eq? @_m "require_relative"))
(call method: (identifier) @call)
(constant) @ref
""",
    "php": """
(namespace_definition name: (namespace_name) @package)
(namespace_use_clause [(qualified_name) (name)] @import)
(function_call_expression function: (name) @call)
(member_call_expression name: (name) @call)
(scoped_call_expression scope: (name) @ref)
(object_creation_expression (name) @ref)
(named_type (name) @ref)
""",
    "swift": """
(import_declaration (identifier) @import)
(call_expression (simple_identifier) @call)
(user_type (type_identifier) @ref)
""",
    "scala": """
(package_clause name: (package_identifier) @package)
(import_declaration) @import.statement
(call_expression function: (identifier) @call)
(call_expression function: (field_expression field: (identifier) @call))
(field_expression value: (identifier) @ref)
(type_identifier) @ref
""",
    "dart": """
(import_specification (configurable_uri (uri (string_literal) @import)))
((identifier) @call . (selector (argument_part)))
(type_identifier) @ref
""",
    "lua": """
(function_call name: (identifier) @_r arguments: (arguments . (string content: (string_content) @import))
  (#eq? @_r "require"))
(function_call name: (identifier) @call)
(function_call name: (dot_index_expression field: (identifier) @call))
(function_call name: (method_index_expression method: (identifier) @call))
""",
    "r": """
(call function: (identifier) @_f arguments: (arguments . (argument value: (identifier) @import.package))
  (#any-of? @_f "library" "require"))
(call function: (identifier) @_f arguments: (arguments . (argument value: (string (string_content) @import.relative)))
  (#eq? @_f "source"))
(call function: (identifier) @call)
""",
    "julia": """
(using_statement (identifier) @import.package)
(import_statement (identifier) @import.package)
(call_expression . (identifier) @_f (argument_list . (string_literal) @import.relative) (#eq? @_f "include"))
(call_expression . (identifier) @call)
""",
    "elixir": """
(call target: (identifier) @_k (arguments . (alias) @import) (#any-of? @_k "alias" "import" "use" "require"))
(call target: (dot left: (alias) @ref right: (identifier) @call))
(call target: (identifier) @call)
""",
    "haskell": """
(header (module) @package)
(import module: (module) @import)
(apply function: (variable) @call)
(name) @ref
""",
    "ocaml": """
(open_module module: (module_path) @import)
(module_path (module_name) @ref)
(application_expression function: (value_path (value_name) @call))
""",
    "zig": """
(SuffixExpr (BUILTINIDENTIFIER) @_b (FnCallArguments (ErrorUnionExpr (SuffixExpr (STRINGLITERALSINGLE) @import.relative)))
  (#eq? @_b "@import"))
(SuffixExpr (IDENTIFIER) @call (FnCallArguments))
(SuffixExpr (IDENTIFIER) @ref)
""",
    "bash": """
(command name: (command_name (word) @_c) . argument: [(word) (string) (raw_string)] @import.relative
  (#any-of? @_c "source" "."))
(command name: (command_name (word) @call))
""",
    "powershell": """
(command (command_name) @call)
""",
}

#: Decision points, named the way each grammar names them; a grammar that has no such node skips it.
#: (kind, named) — `&&` and `||` are anonymous tokens, the statements are named nodes.
BRANCH_NODES: tuple[tuple[str, bool], ...] = tuple(
    [(kind, True) for kind in (
        "if_statement", "if_expression", "if_let_expression", "elif_clause", "else_if_clause", "elsif",
        "if", "unless", "if_modifier", "unless_modifier", "conditional", "conditional_expression",
        "ternary_expression", "for_statement", "for_in_statement", "for_of_statement", "enhanced_for_statement",
        "foreach_statement", "for_expression", "for_range_loop", "range_based_for_statement", "for",
        "while_statement", "while_expression", "while", "until", "do_statement", "do_while_statement",
        "repeat_statement", "loop_expression", "case_clause", "switch_case", "switch_section", "case_item",
        "case_statement", "when", "when_entry", "match_arm", "catch_clause", "rescue", "except_clause",
        "guard_statement", "IfStatement", "ForStatement", "WhileStatement", "SwitchProng")]
    + [(op, False) for op in ("&&", "||", "and", "or")])

#: A Go type spec's kind by what it is; a Swift declaration by its keyword; a Zig container is a struct.
SHAPES = {"struct_type": "struct", "interface_type": "interface", "struct": "struct", "enum": "enum",
          "class": "class", "actor": "class", "ContainerDecl": "struct"}
#: Kinds that hold methods: a function inside one of these is a method of it.
TYPES = {"class", "struct", "interface", "trait", "enum", "object"}
#: Kinds whose nested declarations are named under them.
CONTAINERS = TYPES | {"module", "scope"}
PRIVATE = re.compile(r"\b(private|fileprivate|protected)\b")
MAX_REFS = 4_000
#: Constants one file may contribute. A generated table — Go's zerrors_linux_amd64.go holds thousands
#: — would otherwise be most of the index, and none of it is what a person searches a repository for.
MAX_CONSTANTS = 100
#: The widest name kept, so one minified or generated file cannot carry a line of code as a symbol.
MAX_NAME = 200
#: How many tokens tree-sitter may have swept into one error before the file is left unread. Asking
#: a query about an error costs time in the square of how wide it is: measured here, 20 000 tokens
#: in one error take a quarter of a second to question, 50 000 take 1.6 s, 200 000 take 25 s — and a
#: megabyte of unclosed brackets, which MAX_PARSE allows, is about twenty minutes of one worker with
#: nothing able to stop it while the project still says it is being read. Real code is nowhere near
#: this: of 4 000 .ts and .js files read to set this number, three in a hundred held an error at
#: all, and the widest of those had swept up three tokens.
MAX_ERROR_WIDTH = 10_000
#: How many nodes the check below may look at. A file that takes more than this to check holds
#: thousands of separate errors, which is not source code either.
MAX_ERROR_STEPS = 10_000


# ── grammars and queries, loaded once, lazily ────────────────────
_LOCK = threading.Lock()


@cache
def _load(grammar: str) -> tuple[Language, Query, Query] | None:
    """The grammar and its two compiled queries, or None when the pack has no such grammar.

    Cached per process: a grammar is loaded the first time a file of its language is read and never
    again. The branch patterns are made from BRANCH_NODES, keeping only the node kinds this grammar
    really has, so one list serves every language.
    """
    try:
        language = get_language(grammar)  # type: ignore[arg-type]
    except LookupError:
        return None
    branches = [f'"{kind}"' if not named else f"({kind})" for kind, named in BRANCH_NODES
                if language.id_for_node_kind(kind, named)]
    flat = FLAT.get(grammar, "") + (f"[{' '.join(branches)}] @branch\n" if branches else "")
    try:
        return language, Query(language, DEFS.get(grammar, "")), Query(language, flat)
    except QueryError as broken:  # a query that does not fit its grammar is a bug here, said plainly
        raise RuntimeError(f"the {grammar} queries do not fit the bundled grammar: {broken}") from broken


def _parser(grammar: str) -> Parser:
    """A parser per grammar per thread: a tree-sitter parser holds state and is not shared."""
    local = _THREAD.__dict__.setdefault("parsers", {})
    if grammar not in local:
        local[grammar] = get_parser(grammar)  # type: ignore[misc,arg-type]
    return local[grammar]


_THREAD = threading.local()


# ── reading one file ─────────────────────────────────────────────
def _text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")


def _unquote(value: str) -> str:
    return value.strip().strip("\"'`<>")


def _modifiers(node: Node) -> str:
    """The words in front of a declaration that say who may see it: `public static`, `pub`, `private`."""
    out = []
    for child in node.children[:4]:
        if "modifier" in child.type or child.type in ("storage_class_specifier", "visibility_modifier"):
            out.append(_text(child))
    return " ".join(out).lower()


def _exported(grammar: str, node: Node, name: str, kind: str, inside: str | None) -> bool:
    """Whether a declaration is part of what its file offers, in the words its language uses."""
    bare = name.rsplit(".", 1)[-1]
    words = _modifiers(node)
    if grammar == "go":
        return bare[:1].isupper()
    if grammar in ("typescript", "tsx", "javascript"):
        if inside:
            return not bare.startswith("#") and "private" not in words
        up = node.parent
        return bool(up and (up.type == "export_statement" or (up.parent and up.parent.type == "export_statement")))
    if grammar == "rust":
        return inside == "trait" or any(child.type == "visibility_modifier" for child in node.children)
    if grammar == "zig":
        before = node.prev_sibling
        return before is not None and before.type == "pub"
    if grammar in ("java", "csharp"):
        return inside == "interface" or bool(re.search(r"\b(public|internal)\b", words))
    if grammar in ("c", "cpp"):
        return "static" not in words
    if grammar == "dart":
        return not bare.startswith("_")
    if grammar == "lua":
        return not (node.children and node.children[0].type == "local")
    if grammar == "elixir":
        target = node.child_by_field_name("target")
        return target is None or _text(target) not in ("defp", "defmacrop")
    return not PRIVATE.search(words)


def _end(node: Node) -> int:
    """The last line of a declaration. A Dart function's body is the node after its signature."""
    last = node.end_point[0]
    after = node.next_named_sibling
    if after is not None and after.type == "function_body":
        last = after.end_point[0]
    return last + 1


def _refine(grammar: str, node: Node, kind: str, shape: Node | None) -> str | None:
    """The kind a declaration really is, where one query pattern covers several; None to skip it."""
    if shape is not None:
        if kind == "type":
            return SHAPES.get(shape.type, "type")
        return SHAPES.get(_text(shape) if grammar == "swift" else shape.type, kind)
    if grammar == "kotlin" and kind == "class":
        kinds = {child.type for child in node.children}
        if "interface" in kinds:
            return "interface"
        if "enum" in _modifiers(node):
            return "enum"
    if grammar == "ocaml" and kind == "function":
        has_params = any(child.type == "parameter" for child in node.children)
        body = node.child_by_field_name("body")
        return "function" if has_params or (body is not None and body.type == "fun_expression") else "constant"
    if grammar == "zig" and kind == "constant" and "@import(" in _text(node):
        return None
    return kind


def _wrecked(root: Node) -> bool:
    """Whether a tree came back too badly broken to be worth asking questions of — see MAX_ERROR_WIDTH.

    Nearly free, because only the branches that hold an error are walked: `has_error` says which
    those are, an error's own children are counted rather than visited, and a file that is one long
    error is settled by looking at a single node.
    """
    steps = 0
    stack = [root]
    while stack:
        node = stack.pop()
        if node.is_error:
            if node.child_count > MAX_ERROR_WIDTH:
                return True
            continue
        for child in node.children:
            steps += 1
            if steps > MAX_ERROR_STEPS:
                return True
            if child.has_error:
                stack.append(child)
    return False


def outline(grammar: str, source: bytes, *, component_names: bool = False, line_offset: int = 0) -> Outline | None:
    """Read one file with its grammar. None when this machine has no such grammar, and None when the
    file came back wrecked — a run of unclosed brackets, a binary named .ts — which is recorded the
    same way: a file nothing here could read, so the rest of the project is still read.

    `component_names`: a .tsx/.jsx file, where a function or constant named in capitals is a React
    component. `line_offset`: the file is a block inside another (a Vue or Svelte script), so its
    lines are counted from where the block starts.
    """
    with _LOCK:
        loaded = _load(grammar)
    if loaded is None:
        return None
    _language, defs, flat = loaded
    tree = _parser(grammar).parse(source)
    root = tree.root_node
    if root.has_error and _wrecked(root):
        return None
    out = Outline()

    # Declarations, in the order they appear, each named under the type or module it sits in.
    found: dict[int, tuple[Node, Node, str, Node | None, Node | None]] = {}
    for _pattern, caps in QueryCursor(defs).matches(root):
        name_nodes = caps.get("name")
        if not name_nodes:
            continue
        for key, nodes in caps.items():
            if key == "scope" or key.startswith("def."):
                node = nodes[0]
                kind = "scope" if key == "scope" else key[4:]
                owner = caps.get("owner", [None])[0]
                shape = caps.get("shape", [None])[0]
                prior = found.get(node.id)
                # One node matched as a function and as an exported constant is a function.
                if prior is None or (prior[2] == "constant" and kind != "constant"):
                    found[node.id] = (node, name_nodes[0], kind, owner, shape)
    stack: list[tuple[int, str, str]] = []           # (end byte, name as declared, kind) of what we are inside
    seen: set[tuple[str, int]] = set()
    constants = 0
    for node, name_node, kind, owner, shape in sorted(found.values(), key=lambda f: (f[0].start_byte, -f[0].end_byte)):
        while stack and stack[-1][0] <= node.start_byte:
            stack.pop()
        refined = _refine(grammar, node, kind, shape)
        if refined is None:
            continue
        kind = refined
        inside = stack[-1] if stack else None
        if inside and inside[2] in ("function", "method"):
            continue                                  # a local declaration inside a body is not the file's
        name = declared = _text(name_node).replace("::", ".").strip()
        if not name:
            continue
        if owner is not None:
            name = f"{_text(owner)}.{name}"
            if kind == "function" and grammar != "lua":
                kind = "method"
        elif inside is not None:
            name = f"{inside[1]}.{name}"
            if kind == "function" and inside[2] in TYPES | {"scope"}:
                kind = "method"
        elif kind == "function" and "." in name:      # C++'s Shape::area, defined outside its class
            kind = "method"
        # A body is on the stack too, so that what it declares is dropped above rather than named as
        # the file's own. It is never named under it: the guard `continue`s before any of that.
        if kind in CONTAINERS or kind in ("function", "method"):
            stack.append((node.end_byte, declared, kind))
        if kind == "scope":
            continue
        if component_names and kind in ("function", "constant") and name[:1].isupper() and "." not in name:
            kind = "component"
        line = node.start_point[0] + 1 + line_offset
        if (name, line) in seen:
            continue
        if kind == "constant":
            constants += 1
            if constants > MAX_CONSTANTS:
                continue
        seen.add((name, line))
        out.symbols.append((name[:MAX_NAME], kind, line,
                            _exported(grammar, node, name, kind, inside[2] if inside else None),
                            _end(node) + line_offset))

    # Imports, calls, names used, the package, and the branches — one pass over the tree, in C.
    caps: dict[str, list[Node]] = QueryCursor(flat).captures(root)
    for key, nodes in caps.items():
        if key.startswith("import"):
            how = key.partition(".")[2]
            for node in nodes:
                if how == "statement":
                    out.imports.append(("", _statement(_text(node))))
                else:
                    out.imports.append((how, _unquote(_text(node))))
        elif key in ("call", "ref"):
            for node in nodes[:MAX_REFS]:
                out.refs.add(_text(node))
        elif key == "package" and nodes:
            out.package = re.sub(r"\s+", "", _text(nodes[0])).replace("\\", ".")
        elif key == "branch":
            out.branches = len(nodes)
    return out


def _statement(text: str) -> str:
    """`import static com.acme.Tax.*;` → com.acme.Tax.*; Scala's and Kotlin's the same, without `as`."""
    text = re.sub(r"^\s*import\s+(static\s+)?", "", text).strip().rstrip(";").strip()
    return re.split(r"\s+as\s+|\s+", text)[0]

