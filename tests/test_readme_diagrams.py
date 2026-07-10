from pathlib import Path


def test_readme_links_target_product_and_technical_diagrams() -> None:
    raw = Path("README.md").read_text(encoding="utf-8")
    readme = raw.lower()

    assert "target architecture diagrams" in readme
    assert "docs/assets/architecture/product-flow.svg" in readme
    assert "docs/assets/architecture/technical-architecture.svg" in readme
    assert Path("docs/assets/architecture/product-flow.svg").is_file()
    assert Path("docs/assets/architecture/technical-architecture.svg").is_file()
    assert "mediador não faz fetch externo" in readme
    assert "contrato único" in readme
    assert "busca fresca própria" in readme
    assert "deepseek v4 pro" in readme
    assert "#CopaComAchismo" in raw
    old_hashtag = "#CopaComAchismo".replace("#C", "#c", 1)
    assert old_hashtag not in raw
    assert "make doctor" in readme
    assert "make force" in readme
    assert "data/.run.lock" in readme
    assert "blind_peer_review_enabled=false" in readme
    assert "llm_council_fast_path_enabled=false" in readme
    assert "numeric_chairman_enabled=true" in readme
    assert "prepayment credits are depleted" in readme
    assert "70/30" not in readme
    assert "70% números, 30% contexto" not in readme
    assert "70% estatística" not in readme
    assert "30% qualitativo" not in readme
    assert "opta" not in readme
    assert "deepseek latest free" not in readme
