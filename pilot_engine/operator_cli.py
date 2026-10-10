"""Local, explicitly confirmed operator console for the synthetic workflow."""

from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from pilot_engine.config import AppConfig, Environment
from pilot_engine.execute_documents import ExecuteDocuments
from pilot_engine.execute_operations import ExecuteOperations
from pilot_engine.execute_order import ExecuteOrder
from pilot_engine.find_handoff import FindHandoff
from pilot_engine.inbound import InboundResponses
from pilot_engine.manual_contact import ManualContact
from pilot_engine.quotations import Quotations
from pilot_engine.rfq import SellRFQ
from pilot_engine.sell_delivery import CaptureMailbox, SellDelivery
from pilot_engine.sell_drafts import SellDrafts


READS = {
    "handoff": (FindHandoff, "read"),
    "draft": (SellDrafts, "read"),
    "send-attempt": (SellDelivery, "read"),
    "inbound": (InboundResponses, "read"),
    "opportunity": (InboundResponses, "opportunity_summary"),
    "manual-action": (ManualContact, "read"),
    "rfq": (SellRFQ, "read"),
    "quote": (Quotations, "read"),
    "po": (ExecuteOrder, "read"),
    "order": (ExecuteOrder, "read_order"),
    "operations": (ExecuteOperations, "summary"),
    "document": (ExecuteDocuments, "read"),
    "case": (ExecuteDocuments, "case_summary"),
}

# Each action names the service method, its source record, and the JSON key for
# that record. There is no arbitrary method dispatch or database write escape.
ACTIONS = {
    "handoff-record": (FindHandoff, "record", None, None),
    "draft-create": (SellDrafts, "create", "handoff", "handoff_id"),
    "draft-revise": (SellDrafts, "revise", "draft", "draft_id"),
    "draft-reject": (SellDrafts, "reject", "draft", "draft_id"),
    "send-approve": (SellDelivery, "decide", "draft", "draft_id"),
    "send-capture": (SellDelivery, "dispatch_synthetic", "draft", "draft_id"),
    "opportunity-open": (ManualContact, "open_opportunity", "handoff", "handoff_id"),
    "manual-plan": (ManualContact, "plan", "opportunity", "opportunity_id"),
    "manual-record": (ManualContact, "record", "manual-action", "action_id"),
    "inbound-record": (InboundResponses, "record_inbound", None, None),
    "inbound-review": (InboundResponses, "review", "inbound", "inbound_id"),
    "opt-out-resolve": (InboundResponses, "resolve_opt_out", "inbound", "inbound_id"),
    "rfq-save": (SellRFQ, "save", "inbound", "inbound_id"),
    "rfq-decide": (SellRFQ, "decide", "rfq", "rfq_id"),
    "price-register": (Quotations, "register_price_authority", None, None),
    "quote-save": (Quotations, "save", "rfq", "rfq_id"),
    "quote-decide": (Quotations, "decide", "quote", "quotation_id"),
    "po-save": (ExecuteOrder, "save", "quote", "quotation_id"),
    "po-decide": (ExecuteOrder, "decide", "po", "po_id"),
    "erp-handoff": (ExecuteOperations, "manual_erp_handoff", "order", "order_id"),
    "readiness-record": (ExecuteOperations, "readiness", "order", "order_id"),
    "freight-plan": (ExecuteOperations, "plan_freight", "order", "order_id"),
    "booking-record": (ExecuteOperations, "confirm_booking", "operations", "order_id"),
    "invoice-draft": (ExecuteDocuments, "invoice", "order", "order_id"),
    "packing-draft": (ExecuteDocuments, "packing", "operations", "order_id"),
    "checklist-draft": (ExecuteDocuments, "checklist", "order", "order_id"),
    "document-review": (ExecuteDocuments, "review", "document", "document_id"),
    "metric-record": (ExecuteDocuments, "record_case_metric", "case", "order_id"),
}

QUEUES = {
    "handoff": ("find_handoff", "id"),
    "draft": ("sell_draft", "id"),
    "opportunity": ("sell_opportunity", "id"),
    "manual-action": ("manual_contact_action", "id"),
    "inbound": ("sell_inbound_message", "id"),
    "rfq": ("sell_rfq", "id"),
    "quote": ("sell_quotation", "id"),
    "po": ("execute_po", "id"),
    "order": ("execute_local_order", "id"),
    "document": ("execute_document", "id"),
}


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _read(store: object, kind: str, record_id: str) -> object:
    service, method = READS[kind]
    return getattr(service(store), method)(record_id)


def _form(action: str) -> dict:
    service, method, _, _ = ACTIONS[action]
    signature = inspect.signature(getattr(service, method))
    result = {}
    for key, parameter in signature.parameters.items():
        if key in ("self", "mailbox"):
            continue
        result[key] = ("<required>" if parameter.default is inspect.Parameter.empty
                       else parameter.default)
    if action == "price-register":
        result["content_file"] = "<path to synthetic price evidence>"
        result.pop("content")
    if action == "po-save":
        result["original_file"] = "<path to synthetic PO evidence>"
        result.pop("original")
    return result


def _arguments(action: str, payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Action input must be a JSON object")
    args = dict(payload)
    for action_name, file_key, byte_key in (
            ("price-register", "content_file", "content"),
            ("po-save", "original_file", "original")):
        if action == action_name:
            if byte_key in args or file_key not in args:
                raise ValueError(f"Provide {file_key} instead of {byte_key}")
            args[byte_key] = Path(args.pop(file_key)).read_bytes()
    return args


def _act(store: object, action: str, payload: dict) -> object:
    service, method_name, source_kind, source_key = ACTIONS[action]
    instance = service(store)
    method = getattr(instance, method_name)
    args = _arguments(action, payload)
    if action == "send-capture":
        args["mailbox"] = CaptureMailbox()
    inspect.signature(method).bind(**args)
    source = _read(store, source_kind, payload[source_key]) if source_kind else None
    if source_kind and source is None:
        raise ValueError("Source record does not exist")
    print("SYNTHETIC LOCAL CASE — no real outreach, ERP, booking or document release")
    if source is not None:
        _print({"source_kind": source_kind, "source": source})
    if action == "send-approve" and payload.get("decision") == "APPROVE":
        _print({"exact_send_preview": instance.preview(payload["draft_id"],
                                                       payload["revision"])})
    _print({"action": action, "input": payload})
    if input(f"Type CONFIRM {action} to record this action: ").strip() != f"CONFIRM {action}":
        raise ValueError("Action cancelled; no change recorded")
    return method(**args)


def _feedback(store: object, data_dir: Path, payload: dict) -> dict:
    if (not isinstance(payload, dict) or set(payload) != {"order_id", "friction", "suggestion"}
            or not isinstance(payload["order_id"], str)
            or any(not isinstance(payload[key], str) or not payload[key].strip()
                   or len(payload[key]) > 2000 for key in ("friction", "suggestion"))):
        raise ValueError("Feedback needs order_id, friction and suggestion")
    actor = store._require_access("EDIT_EXECUTE", payload["order_id"])
    case = ExecuteDocuments(store).case_summary(payload["order_id"])
    _print({"case_status": case["technical_case_accepted"],
            "unresolved_issues": case["issues"], "feedback": payload})
    if input("Type CONFIRM feedback to record this observation: ").strip() != "CONFIRM feedback":
        raise ValueError("Feedback cancelled; no change recorded")
    record = {**payload, "data_origin": "SYNTHETIC", "actor_id": actor,
              "recorded_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(data_dir / "operator-feedback.jsonl", flags, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return record


def main(argv: list[str] | None = None, *, config: AppConfig | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synthetic Find → Sell → Execute operator console")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("actions", help="List available operator actions")
    form = commands.add_parser("form", help="Print the JSON input fields for an action")
    form.add_argument("action", choices=sorted(ACTIONS))
    queue = commands.add_parser("queue", help="List case records and current statuses")
    queue.add_argument("kind", choices=sorted(QUEUES))
    show = commands.add_parser("show", help="Review a record, its origin, source and status")
    show.add_argument("kind", choices=sorted(READS))
    show.add_argument("record_id")
    preview = commands.add_parser("send-preview", help="Review the exact synthetic mail envelope")
    preview.add_argument("draft_id")
    preview.add_argument("revision", type=int)
    act = commands.add_parser("act", help="Confirm one action from a JSON form")
    act.add_argument("action", choices=sorted(ACTIONS))
    act.add_argument("input_file", type=Path)
    feedback = commands.add_parser("feedback", help="Record operator friction after a case")
    feedback.add_argument("input_file", type=Path)
    args = parser.parse_args(argv)
    if args.command == "actions":
        _print(sorted(ACTIONS))
        return 0
    if args.command == "form":
        _print(_form(args.action))
        return 0
    config = config if config is not None else AppConfig.from_env()
    if config.environment is Environment.PILOT:
        raise ValueError("Operator console is synthetic only; live mode is tracked in #42")
    store = config.open_store()
    if args.command == "queue":
        table, column = QUEUES[args.kind]
        with closing(store._connect()) as db:
            ids = [row[0] for row in db.execute(f"SELECT {column} FROM {table} ORDER BY rowid")]
        entries = []
        for record_id in ids:
            record = _read(store, args.kind, record_id)
            entries.append({"id": record_id, "status": record.get("status", "UNREVIEWED"),
                            "data_origin": record.get("data_origin", "SYNTHETIC"),
                            "actor_id": record.get("actor_id")})
        _print(entries)
    elif args.command == "show":
        record = _read(store, args.kind, args.record_id)
        if record is None:
            raise ValueError("Record not found")
        print("SYNTHETIC LOCAL CASE — source references are operator supplied")
        _print(record)
    elif args.command == "send-preview":
        _print(SellDelivery(store).preview(args.draft_id, args.revision))
    elif args.command == "feedback":
        payload = json.loads(args.input_file.read_text(encoding="utf-8"))
        _print({"feedback_recorded": _feedback(store, config.data_dir, payload)})
    else:
        payload = json.loads(args.input_file.read_text(encoding="utf-8"))
        _print({"result": _act(store, args.action, payload)})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, TypeError, OSError, json.JSONDecodeError, EOFError) as exc:
        print(f"Operator action stopped: {exc}", file=sys.stderr)
        sys.exit(2)
