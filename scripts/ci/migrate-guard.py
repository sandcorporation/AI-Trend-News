#!/usr/bin/env python3
"""파괴적 Django migration 검사 — PR에서 새로 생긴 migration 파일을 받아, 옛 코드와 호환되지 않는 연산이 있으면 실패한다.

    python3 migrate-guard.py <migration 파일>...      (보통은 migrate-guard.sh <기준 rev> 가 새 파일을 골라 부른다)

배포는 옛 서비스가 도는 동안 migrate 하고, 헬스가 실패하면 옛 이미지로 자동 롤백한다(migrate 는 되돌리지 않는다).
그래서 새 스키마는 옛 코드가 그대로 읽고 쓸 수 있어야 한다. 파괴적이라고 보는 것:

  RemoveField · DeleteModel · RenameField · RenameModel           옛 코드가 아직 그 열·표를 읽는다
  AlterField 로 null 가능 → 불가                                  옛 코드가 NULL 을 넣는다(앞 정의를 못 찾으면 실패로 본다)
  RunSQL 의 DROP · RENAME(주석 밖), 글자 그대로가 아닌 SQL           읽을 수 없으면 실패로 본다
  AddField 가 null 불가인데 db_default 가 없다(ManyToMany 제외)     롤백된 옛 코드의 INSERT 가 그 열을 몰라 실패한다
                                                                  (이 PR 의 새 migration 이 CreateModel 한 모델은 제외 — 옛 코드는 그 표를 모른다)
  SeparateDatabaseAndState 는 database_operations 만 본다.

통과 표시: 파일 어디든 `# sandcorp: destructive-ok — <이유>` 한 줄(이유 필수, 구분자 — · - · :).
뜻은 「코드에서 이미 안 쓰게 한 배포가 끝났다」(expand/contract 의 contract 단계) — jenkins-shared README.

종료 코드: 0 통과 · 1 파괴적 연산(또는 읽지 못한 파일) · 2 사용법. python 3.8+ 표준 라이브러리만 쓴다(맥 노드 3.9).
"""
from __future__ import annotations

import ast
import os
import re
import sys
from typing import Dict, Iterator, List, Optional, Set, Tuple

MARK = re.compile(r"^[ \t]*#[ \t]*sandcorp:[ \t]*destructive-ok\b(.*)$", re.MULTILINE)
MARK_REASON = re.compile(r"^[ \t]*(?:—|–|--?|:)[ \t]*(\S.*)$")
SQL_BAD = re.compile(r"\b(DROP|RENAME)\b", re.IGNORECASE)
NO_COLUMN_FIELDS = {"ManyToManyField", "GeneratedField"}

DROPPED = object()  # 앞 migration 에서 지운 필드
FieldState = Dict[Tuple[str, str], object]  # (모델, 필드) 소문자 → 필드 ast.Call 또는 DROPPED
NewModels = Set[Tuple[str, str]]  # (migrations 폴더, 모델 소문자) — 이 PR 의 새 migration 이 CreateModel 한 것


def tail(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def arg(call: ast.Call, pos: int, name: str) -> Optional[ast.AST]:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    if pos < len(call.args):
        return call.args[pos]
    return None


def text(node: Optional[ast.AST]) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return "?"


def key(model: Optional[ast.AST], field: Optional[ast.AST]) -> Tuple[str, str]:
    return text(model).lower(), text(field).lower()


def is_true(node: Optional[ast.AST]) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def operations(tree: ast.Module) -> List[ast.Call]:
    """class Migration 의 operations = [...] 에서 연산 호출들."""
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Migration":
            for st in node.body:
                value = None
                if isinstance(st, ast.Assign) and any(tail(t) == "operations" for t in st.targets):
                    value = st.value
                elif isinstance(st, ast.AnnAssign) and tail(st.target) == "operations":
                    value = st.value
                if isinstance(value, (ast.List, ast.Tuple)):
                    return [e for e in value.elts if isinstance(e, ast.Call)]
    return []


def flat(ops: List[ast.Call], db_only: bool) -> Iterator[ast.Call]:
    """SeparateDatabaseAndState 를 펼친다. db_only 면 database_operations 만, 아니면 state_operations 만(상태 추적용)."""
    for op in ops:
        if tail(op.func) == "SeparateDatabaseAndState":
            inner = arg(op, 0 if db_only else 1, "database_operations" if db_only else "state_operations")
            if isinstance(inner, (ast.List, ast.Tuple)):
                yield from flat([e for e in inner.elts if isinstance(e, ast.Call)], db_only)
        else:
            yield op


def apply(state: FieldState, op: ast.Call) -> None:
    """앞 migration 들이 정의한 필드를 따라간다(AlterField 의 이전 null 을 알려고)."""
    kind = tail(op.func)
    if kind == "CreateModel":
        model = text(arg(op, 0, "name")).lower()
        fields = arg(op, 1, "fields")
        if isinstance(fields, (ast.List, ast.Tuple)):
            for f in fields.elts:
                if isinstance(f, ast.Tuple) and len(f.elts) == 2 and isinstance(f.elts[1], ast.Call):
                    state[(model, text(f.elts[0]).lower())] = f.elts[1]
    elif kind in ("AddField", "AlterField"):
        field = arg(op, 2, "field")
        if isinstance(field, ast.Call):
            state[key(arg(op, 0, "model_name"), arg(op, 1, "name"))] = field
    elif kind == "RemoveField":
        state[key(arg(op, 0, "model_name"), arg(op, 1, "name"))] = DROPPED
    elif kind == "RenameField":
        model = arg(op, 0, "model_name")
        old = key(model, arg(op, 1, "old_name"))
        if old in state:
            state[key(model, arg(op, 2, "new_name"))] = state.pop(old)
    elif kind == "RenameModel":
        old, new = text(arg(op, 0, "old_name")).lower(), text(arg(op, 1, "new_name")).lower()
        for (m, f) in [k for k in state if k[0] == old]:
            state[(new, f)] = state.pop((m, f))
    elif kind == "DeleteModel":
        model = text(arg(op, 0, "name")).lower()
        for k in [k for k in state if k[0] == model]:
            state[k] = DROPPED


def sql_strings(node: Optional[ast.AST]) -> Optional[List[str]]:
    """RunSQL 의 sql 인자에서 문자열들. 글자 그대로가 아닌 부분이 있으면 None. (sql, params) 튜플은 sql 만."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.List, ast.Tuple)):
        out: List[str] = []
        for e in node.elts:
            if isinstance(e, ast.Tuple) and e.elts:
                e = e.elts[0]
            got = sql_strings(e)
            if got is None:
                return None
            out += got
        return out
    return None


def strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", sql)


def judge(op: ast.Call, state: FieldState, new_models: Set[str]) -> Optional[str]:
    kind = tail(op.func)
    if kind == "RemoveField":
        m, f = key(arg(op, 0, "model_name"), arg(op, 1, "name"))
        return f"RemoveField {m}.{f} — 옛 코드가 아직 이 열을 읽는다"
    if kind == "DeleteModel":
        return f"DeleteModel {text(arg(op, 0, 'name'))} — 옛 코드가 아직 이 표를 읽는다"
    if kind == "RenameField":
        m, f = key(arg(op, 0, "model_name"), arg(op, 1, "old_name"))
        return f"RenameField {m}.{f} → {text(arg(op, 2, 'new_name'))} — 옛 코드는 옛 이름을 읽는다"
    if kind == "RenameModel":
        return f"RenameModel {text(arg(op, 0, 'old_name'))} → {text(arg(op, 1, 'new_name'))} — 옛 코드는 옛 표 이름을 읽는다"
    if kind in ("AddField", "AlterField"):
        field = arg(op, 2, "field")
        if not isinstance(field, ast.Call) or tail(field.func) in NO_COLUMN_FIELDS or is_true(arg(field, 99, "null")):
            return None
        m, f = key(arg(op, 0, "model_name"), arg(op, 1, "name"))
        if kind == "AddField":
            if arg(field, 99, "db_default") is not None or m in new_models:
                return None
            return (
                f"AddField {m}.{f} null 불가인데 db_default 가 없다 — 롤백된 옛 코드의 INSERT 가 그 열을 몰라 실패한다"
                "(db_default 를 주거나, null=True 로 먼저 더하고 다음 배포에서 null 불가로)"
            )
        before = state.get((m, f))
        if before is None or before is DROPPED:
            return f"AlterField {m}.{f} null 불가 — 앞 migration 에서 이 필드 정의를 찾지 못해 null 전환인지 모른다"
        if isinstance(before, ast.Call) and is_true(arg(before, 99, "null")):
            return f"AlterField {m}.{f} null 가능 → 불가 — 옛 코드가 NULL 을 넣는다"
        return None
    if kind == "RunSQL":
        sqls = sql_strings(arg(op, 0, "sql"))
        if sqls is None:
            return "RunSQL — SQL 을 글자 그대로 읽을 수 없다(변수·f-string)"
        for s in sqls:
            hit = SQL_BAD.search(strip_sql_comments(s))
            if hit:
                return f"RunSQL {hit.group(1).upper()} — {' '.join(s.split())[:80]}"
    return None


def parse(path: str) -> ast.Module:
    with open(path, encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def prior_state(path: str) -> FieldState:
    """같은 앱 migrations 폴더에서 이름순으로 이 파일보다 앞선 파일들의 필드 상태."""
    d, name = os.path.split(path)
    state: FieldState = {}
    for other in sorted(os.listdir(d or ".")):
        if not other.endswith(".py") or other == "__init__.py" or other >= name:
            continue
        try:
            tree = parse(os.path.join(d, other))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for op in flat(operations(tree), db_only=False):
            apply(state, op)
    return state


def created_models(paths: List[str]) -> NewModels:
    """새 migration 들이 CreateModel 한 모델. 그 모델에 더하는 필드는 옛 코드가 모르는 표라 null 불가여도 안전하다."""
    out: NewModels = set()
    for path in paths:
        try:
            tree = parse(path)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for op in flat(operations(tree), db_only=True):
            if tail(op.func) == "CreateModel":
                out.add((os.path.dirname(os.path.abspath(path)), text(arg(op, 0, "name")).lower()))
    return out


def check(path: str, new_models: Optional[NewModels] = None) -> Tuple[str, List[str]]:
    """(판정, 줄들). 판정: ok | mark | bad."""
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src, filename=path)
    except (OSError, SyntaxError, UnicodeDecodeError) as e:
        return "bad", [f"{path}: 읽지 못했다 — {type(e).__name__}: {e}"]
    marks = [MARK_REASON.match(m.group(1)) for m in MARK.finditer(src)]
    reasons = [m.group(1).strip() for m in marks if m]
    if reasons:
        return "mark", [reasons[0]]
    state = prior_state(path)
    found: List[str] = []
    here = os.path.dirname(os.path.abspath(path))
    mine = {m for (d, m) in (new_models if new_models is not None else created_models([path])) if d == here}
    ops = operations(tree)
    db_ops = set(id(o) for o in flat(ops, db_only=True))
    state_ops = set(id(o) for o in flat(ops, db_only=False))
    for op in _in_order(ops):
        if id(op) in db_ops:
            why = judge(op, state, mine)
            if why:
                found.append(f"{path}:{op.lineno} {why}")
        if id(op) in state_ops:
            apply(state, op)
    if found and MARK.search(src):
        found.append(f"{path}: 통과 표시에 이유가 없다 — `# sandcorp: destructive-ok — <이유>`")
    return ("bad" if found else "ok"), found


def _in_order(ops: List[ast.Call]) -> Iterator[ast.Call]:
    for op in ops:
        if tail(op.func) == "SeparateDatabaseAndState":
            for name, pos in (("database_operations", 0), ("state_operations", 1)):
                inner = arg(op, pos, name)
                if isinstance(inner, (ast.List, ast.Tuple)):
                    yield from _in_order([e for e in inner.elts if isinstance(e, ast.Call)])
        else:
            yield op


def main(argv: List[str]) -> int:
    if any(a in ("-h", "--help") for a in argv):
        print(__doc__)
        return 2
    if not argv:
        print("migrate-guard: 새 migration 없음")
        return 0
    bad = 0
    print(f"migrate-guard: 새 migration {len(argv)}개")
    new_models = created_models(argv)
    for path in argv:
        verdict, lines = check(path, new_models)
        if verdict == "mark":
            print(f"  표시  {path} — {lines[0]}")
            continue
        if verdict == "ok":
            print(f"  통과  {path}")
        else:
            bad += 1
        for line in lines:
            print(f"  파괴  {line}")
    if bad:
        print(
            f"파괴적 migration {bad}개 — 옛 코드(자동 롤백 대상)가 새 스키마와 맞지 않는다. expand/contract 두 단계로 나눈다:\n"
            "  1) 코드에서 그 열·표를 안 쓰게 하는 PR 을 배포한다(스키마는 그대로, 새 열은 null 가능·db_default 로).\n"
            "     null 불가 새 열은 db_default 를 주거나, null=True 로 먼저 더해 배포한 뒤 다음 PR 에서 null 불가로 바꾼다(그때 표시를 단다).\n"
            "  2) 그 배포가 끝난 뒤의 PR 에서 지우기·바꾸기 migration 에 `# sandcorp: destructive-ok — <이유>` 를 단다.\n"
            "  자세히: https://github.com/sandcorporation/jenkins-shared#파괴적-migrate-검사"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
