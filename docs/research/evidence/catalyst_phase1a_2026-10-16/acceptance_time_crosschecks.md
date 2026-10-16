# Acceptance-time cross-checks (2026-10-16, real SEC data, read-only)

Method: for each filing compare (a) the submissions JSON `acceptanceDateTime` ("Z"), (b) the filing's SGML header `<ACCEPTANCE-DATETIME>` (Eastern wall clock, converted with America/New_York), (c) the
filing index page "Accepted" line, (d) the EDGAR "current filings" Atom `<updated>` (explicit UTC offset). Scripts: `crosscheck_scripts/`. The contact in the User-Agent is not stored anywhere in these files.

## 1. All 123 stored filings (2026-09-01 .. 2026-10-15): feed minus header-derived UTC
{4.0 h: 81, 0.0 h: 42}   by form: 8-K +4.0 x69 / +0.0 x39; 8-K/A +4.0 x4; 10-Q +4.0 x7 / +0.0 x3; 10-K +4.0 x1
Wall-clock hour (ET) of the shifted ones is spread over 06:00-21:00; weekdays Mon-Fri. No time-of-day or weekday rule separates shifted from exact.

## 2. Header vs page vs feed for the first four sampled filings
0001571996-26-000039 8-K   feed 2026-09-02 00:10:14Z  header 20260901161014  page "Accepted 2026-09-01 16:10:14"   -> true UTC 20:10:14, feed +4 h
0001140361-26-035325 8-K/A feed 2026-09-02 00:30:35Z  header 20260901163035  page "Accepted 2026-09-01 16:30:35"   -> feed +4 h
0001628280-26-059879 8-K   feed 2026-09-02 00:50:42Z  header 20260901165042  page "Accepted 2026-09-01 16:50:42"   -> feed +4 h
0000014693-26-000048 8-K   feed 2026-09-02 12:03:11Z  header 20260902080311  page "Accepted 2026-09-02 08:03:11"   -> equal

## 3. Filings listed by the Atom feed in the last hours, feed value as served at the time of the check (feed minus Atom instant)
age < 1 h {0.0: 11}   1-3 h {0.0: 24}   3-6 h {0.0: 7}   6-12 h {0.0: 1}   12 h+ {0.0: 7, +4.0: 10}

## 4. Old filings of three large filers (feed minus header-derived UTC)
< 30 d: none sampled   30-90 d {+4.0: 3}   90-365 d {+4.0: 2}   1 y+ {+4.0: 13, +5.0: 6}   (24 of 24 shifted)

## 5. Brand-new filings caught by the latency probe: feed minus Atom instant
{0.0: 11} -- no shift at first sight.

Conclusion used by the design: the feed value is right when a filing first appears and is later replaced, for most filings, by a value later by the Eastern UTC offset. It is never earlier than the SEC's own record.
`acceptance_check.py` therefore reconciles against the header record and stores both values.
