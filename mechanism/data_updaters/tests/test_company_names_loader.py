"""company_names_loader: name cleaning (share-class boilerplate, all-caps listing names) and symbol matching. Pure, no I/O."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from company_names_loader import clean_name, match  # noqa: E402


def test_strips_only_whole_trailing_share_class_phrases():
    assert clean_name("Braze, Inc. Class A Common Stock") == "Braze, Inc."
    assert clean_name("Unity Software Inc. Common Stock, par value $0.000005 per share") == "Unity Software Inc."
    assert clean_name("Alibaba Group Holding Limited American Depositary Shares each representing eight Ordinary share") == "Alibaba Group Holding Limited"
    assert clean_name("Spotify Technology S.A. Ordinary Shares") == "Spotify Technology S.A."
    assert clean_name("Bio-Rad Laboratories, Inc.Class A") == "Bio-Rad Laboratories, Inc."
    # words that merely contain a pattern are never cut
    assert clean_name("Unit Corporation") == "Unit Corporation"
    assert clean_name("Broadstone Net Lease, Inc. Common Stock") == "Broadstone Net Lease, Inc."
    assert clean_name("Common Stock") == "Common Stock"  # never empty


def test_all_caps_listing_names_get_normal_case_keeping_acronyms():
    assert clean_name("PAYCOM SOFTWARE, INC.", "PAYC") == "Paycom Software, Inc."
    assert clean_name("CBRE GROUP, INC.", "CBRE") == "CBRE Group, Inc."
    assert clean_name("TRONOX LIMITED CL A", "TROX") == "Tronox Limited"
    assert clean_name("SARATOGA INVESTMENT CORP. NEW", "SAR") == "Saratoga Investment Corp."
    assert clean_name("CNH INDUSTRIAL N.V.", "CNH") == "CNH Industrial N.V."
    assert clean_name("BANK OF AMERICA CORPORATION", "BAC") == "Bank of America Corporation"
    assert clean_name("AT&T INC.", "T") == "AT&T Inc."
    assert clean_name("QUAD/GRAPHICS, INC.", "QUAD") == "Quad/Graphics, Inc."
    assert clean_name("Myriad Genetics, Inc.", "MYGN") == "Myriad Genetics, Inc."  # mixed case left alone


def test_match_handles_dot_and_dash_class_spellings_and_skips_unknown_symbols():
    assets = [{"symbol": "BRK.B", "name": "BERKSHIRE HATHAWAY Class B", "exchange": "NYSE"},
              {"symbol": "AAPL", "name": "Apple Inc. Common Stock", "exchange": "NASDAQ"}]
    assert match(["BRK-B", "AAPL", "ZZZZ"], assets) == {"BRK-B": ("Berkshire Hathaway", "NYSE"), "AAPL": ("Apple Inc.", "NASDAQ")}
