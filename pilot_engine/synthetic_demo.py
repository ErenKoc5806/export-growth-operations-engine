"""Seed one openly fictional Find handoff for the visual walkthrough."""

from __future__ import annotations

import hashlib
from contextlib import closing

from pilot_engine.candidates import CandidateDiscovery
from pilot_engine.contact_routes import ContactRoutes
from pilot_engine.find_handoff import FindHandoff
from pilot_engine.profiles import ProductProfiles
from pilot_engine.qualification import BuyerQualification
from pilot_engine.store import PilotStore


SOURCE = "SYN-UI-DEMO-PROFILE"
DOCUMENT = b"invented demo manufacturer document; no real product facts"


def create_demo_handoff(store: PilotStore) -> str:
    """Return the existing sample or create a completely synthetic case."""
    store._require_access("RECORD_FIND_HANDOFF", "SYN-UI-DEMO")
    with closing(store._connect()) as db:
        prior = db.execute("""SELECT h.id FROM find_handoff h
            JOIN product_profile_revision r ON r.product_id = h.product_id
            WHERE r.payload_json LIKE '%SYN-UI-DEMO-PROFILE%'
            ORDER BY h.rowid DESC LIMIT 1""").fetchone()
        if prior:
            return prior["id"]
    profiles = ProductProfiles(store)
    profiles.register_source_document(SOURCE, DOCUMENT)
    profile = {
        "manufacturer_name": "Example Clamp Works", "manufacturer_country": "TR",
        "product_name": "Connection clamp", "sku": "EXAMPLE-001", "unit": "PCS",
        "drawing_ref": "EXAMPLE-DRAWING-1", "dimensions": "20 x 30 mm",
        "material": "Example steel", "intended_use": "General mechanical connection",
        "technical_limits": "Invented training product", "target_country": store.scope.country,
        "buyer_role": "Distributor", "hs6": store.scope.hs6,
        "classification_status": "REVIEWED",
        "classification_note": "Training filter only; no actual tariff classification",
        "source_kind": "MANUFACTURER", "source_ref": SOURCE,
        "source_sha256": hashlib.sha256(DOCUMENT).hexdigest(),
        "minimum_order_quantity": "100", "lead_time_days": 14,
        "payment_terms": "Example advance payment", "incoterm_code": "FCA",
        "incoterm_place": "Example city",
        "claims": [{"text": "Connection clamp", "confirmed_use": True,
                    "evidence_ref": "EXAMPLE-DRAWING-1"}],
        "search_terms": [{"text": "connection clamp", "confirmed_use": True,
                          "evidence_ref": "EXAMPLE-DRAWING-1"}],
    }
    product_id, revision = profiles.save_draft(profile)
    profiles.decide(product_id, revision, "APPROVED", "Invented training data reviewed")
    candidate = CandidateDiscovery(store).record(
        source_system="EXAMPLE_SITE", source_ref="SYN-UI-DEMO-CANDIDATE", query="clamp",
        observed_at_utc="2026-10-04T00:00:00Z", name="Example Buyer",
        country_code=store.scope.country, role_hypothesis="DISTRIBUTOR",
        website="https://example.org", evidence_urls=["https://example.org/products"],
        summary="Invented distributor for UI walkthrough")
    candidate_id = candidate["id"]
    BuyerQualification(store).decide(
        product_id, candidate_id, 1, outcome="ACCEPT",
        checks={"product_spec": "CONFIRMED", "buyer_role": "CONFIRMED",
                "corridor": "CONFIRMED"},
        cited_evidence_ids=[candidate["evidence"][0]["id"]],
        explanation="Invented product and distributor fit for UI demo")
    routes = ContactRoutes(store)
    routes.authorize_source(
        source_system="EXAMPLE_SITE", source_ref="SYN-UI-DEMO-CONTACT",
        source_url="https://example.org/contact", data_class="PERSONAL_ROUTE",
        decision="ALLOW", processing_basis="OTHER_REVIEWED",
        lawful_basis_ref="SYN-UI-BASIS", source_terms_ref="SYN-UI-TERMS",
        retention_until_utc="2030-01-01T00:00:00Z")
    route = routes.record(
        product_id, candidate_id, kind="GENERIC_EMAIL", value="sales@example.org",
        source_system="EXAMPLE_SITE", source_ref="SYN-UI-DEMO-CONTACT",
        source_url="https://example.org/contact", observed_at_utc="2026-10-04T00:00:00Z")
    routes.check(route["id"], method="MANUAL_PAGE", result="ROUTE_CONFIRMED",
                 source_url="https://example.org/contact", checked_at_utc="2026-10-04T00:00:00Z",
                 explanation="Invented source page")
    handoff_id, _ = FindHandoff(store).record(product_id, candidate_id, route["id"])
    return handoff_id
