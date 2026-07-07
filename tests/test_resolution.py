from mnemosyne.resolution import normalize


def test_normalize():
    assert normalize("The Lakehouse Vendor") == "lakehouse vendor"
    assert normalize("Databricks, Inc.") == "databricks"
    assert normalize("  DBX  ") == "dbx"
    assert normalize("Unity   Catalog") == "unity catalog"


def test_exact_match(fabric, provenance):
    result = fabric.resolve_entity("Databricks", provenance)
    assert result.status == "matched"
    assert result.method == "exact"
    assert result.entity.id == "canon:company/databricks"
    assert result.tier == "canonical"


def test_alias_matches(fabric, provenance):
    for mention in ("DBX", "the lakehouse vendor", "Unity Catalog people"):
        result = fabric.resolve_entity(mention, provenance)
        assert result.status == "matched", mention
        assert result.method == "alias", mention
        assert result.entity.id == "canon:company/databricks", mention


def test_entity_type_filter(fabric, provenance):
    result = fabric.resolve_entity("Databricks", provenance, entity_type="technology")
    assert result.status != "matched" or result.entity.entity_type == "technology"


def test_overlay_shadows_canonical(fabric, provenance):
    # A private entity with the same name wins over the canonical one.
    mine = fabric.create_entity("Databricks", "project", provenance)
    result = fabric.resolve_entity("Databricks", provenance)
    assert result.entity.id == mine.id
    assert result.tier == "overlay"


def test_fuzzy_auto_accept_learns_alias(fabric, provenance):
    result = fabric.resolve_entity("Databrics", provenance)
    assert result.status == "matched"
    assert result.method == "fuzzy"
    assert result.score >= 92
    assert result.entity.id == "canon:company/databricks"
    # The misspelling is now a learned alias: instant alias hit next time.
    again = fabric.resolve_entity("Databrics", provenance)
    assert again.method == "alias"


def test_fuzzy_midband_proposes_not_links(fabric, provenance):
    result = fabric.resolve_entity("Datbrcks", provenance)  # scores ~89 vs Databricks
    assert result.status == "proposed"
    assert result.proposal_id is not None
    assert 80 <= result.score < 92
    assert result.entity.id == "canon:company/databricks"
    pending = fabric.overlay.list_proposals("pending")
    assert pending[0].candidate_name == "Datbrcks"


def test_no_match_created_or_not_found(fabric, provenance):
    result = fabric.resolve_entity("Zorbcorp", provenance)
    assert result.status == "not_found"

    created = fabric.resolve_entity("Zorbcorp", provenance, create_if_missing=True)
    assert created.status == "created"
    assert created.entity.id.startswith("usr_")
    assert created.tier == "overlay"

    # Now resolvable exactly.
    assert fabric.resolve_entity("Zorbcorp", provenance).status == "matched"


def test_resolve_or_create_provisional_on_proposal(fabric, provenance):
    result, entity = fabric.resolve_or_create("Datbrcks", provenance)
    assert result.status == "proposed"
    # The write landed on a provisional entity, not the proposed match.
    assert entity.id.startswith("usr_")
    assert entity.id != result.entity.id
