def test_guess_normalization():
    import re
    normalize = lambda s: re.sub(r"\\s+", " ", s.strip()).casefold()
    assert normalize("  WaterMelon ") == normalize("watermelon")
    assert normalize("red   apple") == "red apple"
    assert normalize("apple") != normalize("orange")
