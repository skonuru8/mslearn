from mslearn.adapters.htmltext import html_to_segments, html_to_text


def test_paragraphs_separated_and_scripts_dropped():
    html = (
        "<html><head><title>T</title><script>var x=1;</script></head>"
        "<body><nav>menu junk</nav>"
        "<h1>Heading</h1><p>First   para.</p><p>Second\npara.</p>"
        "<style>.a{color:red}</style></body></html>"
    )
    text = html_to_text(html)
    assert "var x" not in text and "menu junk" not in text and "color:red" not in text
    # Heading text is now purely structural (drives section_path) and no
    # longer appears as its own flattened body paragraph.
    paras = text.split("\n\n")
    assert paras == ["First para.", "Second para."]


def test_empty_html_gives_empty_string():
    assert html_to_text("<html><body></body></html>") == ""


def test_html_to_segments_tracks_heading_paths():
    segments = html_to_segments("<h1>A</h1><p>x</p><h2>B</h2><p>y</p>")
    assert segments == [(("A",), "x"), (("A", "B"), "y")]


def test_html_to_segments_no_heading_is_flat():
    segments = html_to_segments("<p>x</p><p>y</p>")
    assert segments == [((), "x"), ((), "y")]
