from ingestion.source_mapper import CanonicalSourceMapper, LibraryMapping


def test_mapper_resolves_by_package_name() -> None:
    mapper = CanonicalSourceMapper(
        [
            LibraryMapping(
                library_id="nextjs",
                package_names=["next", "nextjs"],
                sources=["https://nextjs.org/docs", "https://nextjs.org/llms.txt"],
            )
        ]
    )

    resolved = mapper.resolve_by_package("next")
    assert resolved is not None
    assert resolved.library_id == "nextjs"


def test_mapper_prefers_llms_full_then_llms_then_docs() -> None:
    mapper = CanonicalSourceMapper([])
    preferred = mapper.preferred_source(
        [
            "https://example.com/docs",
            "https://example.com/llms.txt",
            "https://example.com/llms-full.txt",
        ]
    )
    assert preferred.endswith("/llms-full.txt")
