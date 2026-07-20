from athenaeum.ingest.zotero import zotero_item_to_paper_fields


def test_journal_article_with_doi() -> None:
    data = {
        "itemType": "journalArticle",
        "title": "A Study of Things",
        "creators": [
            {"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"},
            {"creatorType": "author", "firstName": "Alan", "lastName": "Turing"},
        ],
        "publicationTitle": "Journal of Examples",
        "date": "2019-03-14",
        "abstractNote": "We studied things.",
        "DOI": "https://doi.org/10.1234/abcd",
    }
    fields = zotero_item_to_paper_fields(data)
    assert fields["title"] == "A Study of Things"
    assert fields["authors_json"] == {"names": ["Ada Lovelace", "Alan Turing"]}
    assert fields["venue"] == "Journal of Examples"
    assert fields["year"] == 2019
    assert fields["abstract"] == "We studied things."
    assert fields["doi"] == "10.1234/abcd"


def test_webpage_without_doi() -> None:
    data = {
        "itemType": "webpage",
        "title": "Some Blog Post",
        "creators": [{"creatorType": "author", "name": "A Blog"}],
        "websiteTitle": "Example Blog",
        "date": "2021",
        "abstractNote": "",
        "DOI": "",
    }
    fields = zotero_item_to_paper_fields(data)
    assert fields["title"] == "Some Blog Post"
    assert fields["authors_json"] == {"names": ["A Blog"]}
    assert fields["venue"] == "Example Blog"
    assert fields["year"] == 2021
    assert fields["abstract"] is None
    assert fields["doi"] is None


def test_missing_fields_all_default_gracefully() -> None:
    fields = zotero_item_to_paper_fields({})
    assert fields["title"] == "(untitled)"
    assert fields["authors_json"] is None
    assert fields["venue"] is None
    assert fields["year"] is None
    assert fields["abstract"] is None
    assert fields["doi"] is None


def test_creator_with_only_last_name() -> None:
    data = {"creators": [{"creatorType": "author", "lastName": "Prigogine"}]}
    fields = zotero_item_to_paper_fields(data)
    assert fields["authors_json"] == {"names": ["Prigogine"]}
