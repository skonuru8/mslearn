from mslearn.adapters.htmltext import html_to_text


def test_paragraphs_separated_and_scripts_dropped():
    html = (
        "<html><head><title>T</title><script>var x=1;</script></head>"
        "<body><nav>menu junk</nav>"
        "<h1>Heading</h1><p>First   para.</p><p>Second\npara.</p>"
        "<style>.a{color:red}</style></body></html>"
    )
    text = html_to_text(html)
    assert "var x" not in text and "menu junk" not in text and "color:red" not in text
    paras = text.split("\n\n")
    assert paras == ["Heading", "First para.", "Second para."]


def test_empty_html_gives_empty_string():
    assert html_to_text("<html><body></body></html>") == ""
