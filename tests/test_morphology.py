from __future__ import annotations

import pytest

from lingua_calc.morphology import MorphStatus, is_verb_form, missing_voice, parse_morphology


def features(label: str, token_type: str = "") -> dict[str, str]:
    return parse_morphology(label, token_type).features()


# --- the variance that made string matching unsafe -------------------------
#
# All four spellings below appeared in logs/bedrock_payloads.jsonl for the same
# grammatical fact, 3,782 tokens between them.


@pytest.mark.parametrize(
    "label",
    ["pres. act. ind. 3sg", "pres. act. ind. 3sg.", "PRES. ACT. IND. 3SG", "pres act ind 3sg"],
)
def test_present_active_indicative_spellings_agree(label):
    assert features(label) == {
        "tense": "pres",
        "voice": "act",
        "mood": "ind",
        "person": "3",
        "number": "sg",
    }


@pytest.mark.parametrize("label", ["nom. sg. fem.", "nom. sg. f.", "nom sg fem"])
def test_gender_abbreviations_agree(label):
    assert features(label) == {"case": "nom", "number": "sg", "gender": "fem"}


@pytest.mark.parametrize("label", ["def. art. nom. sg. fem.", "definite article nom. sg. fem."])
def test_article_prefix_spellings_agree(label):
    morph = parse_morphology(label, "article")

    assert morph.features() == {"case": "nom", "number": "sg", "gender": "fem"}
    assert morph.descriptors == ["def. art."]
    assert morph.status is MorphStatus.OK


def test_trailing_period_does_not_create_a_distinct_value():
    assert features("pres. ind. 3pl.") == features("pres. ind. 3pl")


# --- ambiguity is preserved, not resolved ----------------------------------


def test_syncretic_case_keeps_both_readings():
    """Greek nom./acc. syncretism is real information; picking one would be a
    silent guess."""
    assert features("nom./acc. sg. neut.")["case"] == "acc|nom"


def test_ambiguous_gender_and_voice():
    assert features("gen. sg. m./n.")["gender"] == "masc|neut"
    assert features("aor. mid./pass. ind. 3sg")["voice"] == "mid|pass"


def test_ambiguous_value_is_canonical_regardless_of_order():
    assert features("acc./nom. pl.")["case"] == features("nom./acc. pl.")["case"] == "acc|nom"


def test_an_unreadable_half_of_an_ambiguity_is_reported_not_dropped():
    """Half a reading silently discarded is worse than none: the token would
    claim full coverage while one of its two readings vanished."""
    morph = parse_morphology("masc./xyz sg.")

    assert morph.features() == {"gender": "masc", "number": "sg"}
    assert morph.unparsed == ["xyz"]
    assert morph.status is MorphStatus.PARTIAL


@pytest.mark.parametrize("spelling", ["deponent", "depon.", "dep."])
def test_deponent_is_middle_in_form_and_flagged_as_a_lexical_class(spelling):
    """Both facts are kept. Voice carries the form, which is what a morphology
    count measures; the flag carries the class. Reading it as mid/pass would
    count passives the text does not contain, and leaving voice unset would
    report a voice gap that is not one."""
    morph = parse_morphology(f"aor. ind. 3sg {spelling}", "verb")

    assert morph.voice == "mid"
    assert not morph.has("voice", "pass")
    assert morph.is_deponent
    assert not missing_voice(morph, "verb")


def test_a_plain_middle_is_not_marked_deponent():
    """The flag has to mean the label said so, or it cannot be counted."""
    assert not parse_morphology("aor. mid. ind. 3sg", "verb").is_deponent
    assert not parse_morphology("aor. mid./pass. ind. 3sg", "verb").is_deponent


def test_a_compound_reading_does_not_nest_when_a_second_reading_joins_it():
    """"mp" already decodes to "mid|pass"; naively rejoining would produce the
    value "mid|mid|pass", which matches nothing and sorts nowhere sensible."""
    assert features("aor. mp. ind. 3sg deponent")["voice"] == "mid|pass"


def test_has_counts_an_ambiguous_token_toward_each_reading():
    morph = parse_morphology("nom./acc. sg. neut.")

    assert morph.has("case", "nom")
    assert morph.has("case", "acc")
    assert not morph.has("case", "gen")


# --- statuses --------------------------------------------------------------


@pytest.mark.parametrize("label", ["-", "", "   ", "n/a"])
def test_declined_parses_are_not_applicable_rather_than_failures(label):
    morph = parse_morphology(label)

    assert morph.status is MorphStatus.NOT_APPLICABLE
    assert morph.features() == {}
    assert morph.unparsed == []


@pytest.mark.parametrize(
    "label,descriptor",
    [
        ("interrogative", "interrogative"),
        ("negative particle", "negative"),
        ("affirmative particle", "affirmative"),
        ("interrog. adv.", "interrogative"),
        ("abbreviation for πρῶτον", "abbreviation"),
    ],
)
def test_lexical_labels_are_descriptive_not_unparsed(label, descriptor):
    """These carry no morphology but were understood; counting them as failures
    would overstate how dirty the data is."""
    morph = parse_morphology(label)

    assert morph.status is MorphStatus.DESCRIPTIVE
    assert descriptor in morph.descriptors
    assert morph.features() == {}


def test_partly_readable_label_reports_what_it_could_not_read():
    morph = parse_morphology("dat. sg. (incomplete)")

    assert morph.status is MorphStatus.PARTIAL
    assert morph.features() == {"case": "dat", "number": "sg"}
    assert morph.unparsed == ["(incomplete)"]


def test_completely_unreadable_label_is_flagged():
    morph = parse_morphology("τι λέγεις")

    assert morph.status is MorphStatus.UNPARSED
    assert morph.features() == {}
    assert morph.unparsed


@pytest.mark.parametrize("label", ["adv.", "adverb", "adverbial"])
def test_adverb_spellings_land_on_one_descriptor(label):
    """Splitting these across buckets is the spelling drift this module removes:
    `descriptor("adverb")` would silently miss whichever spelling lost."""
    assert parse_morphology(label).descriptors == ["adverb"]


def test_a_multi_word_lexical_label_is_understood():
    """Descriptor lookups run per whitespace-split word, so "indeclinable noun"
    has to be collapsed by a phrase rule or its second word reads as a failure."""
    morph = parse_morphology("indeclinable noun")

    assert morph.descriptors == ["indeclinable"]
    assert morph.unparsed == []
    assert morph.status is MorphStatus.DESCRIPTIVE


def test_two_phrases_meaning_the_same_thing_yield_one_descriptor():
    """Both spellings map to "interrogative"; emitting it twice would double the
    token's contribution to that descriptor's corpus count."""
    assert parse_morphology("interrog. adv. interrog. part.").descriptors == ["interrogative"]


def test_descriptor_alongside_real_morphology_still_counts_as_ok():
    morph = parse_morphology("interrog. nom. sg.")

    assert morph.status is MorphStatus.OK
    assert morph.features() == {"case": "nom", "number": "sg"}
    assert morph.descriptors == ["interrogative"]


# --- absent features -------------------------------------------------------


def test_omitted_voice_is_none_not_a_default():
    """23.5% of logged verb tokens never stated voice. Defaulting to 'act.'
    would fabricate data; None keeps the gap visible."""
    morph = parse_morphology("pres. ind. 3sg", "verb")

    assert morph.voice is None
    assert "voice" not in morph.features()
    assert missing_voice(morph, "verb")


def test_stated_voice_is_not_a_gap():
    assert not missing_voice(parse_morphology("pres. act. ind. 3sg", "verb"), "verb")


def test_a_noun_is_not_a_voice_gap():
    assert not missing_voice(parse_morphology("nom. sg. fem.", "noun"), "noun")


@pytest.mark.parametrize("token_type", ["verb", "participle", ""])
def test_a_voiceless_participle_is_a_gap_whatever_the_type_says(token_type):
    """The prompt requires voice on participles and infinitives too, and `type`
    is free text the model picks — letting it decide would make the gap count
    depend on whether the model wrote "verb" or "participle" that day."""
    morph = parse_morphology("pres. part. nom. sg. masc.", token_type)

    assert missing_voice(morph, token_type)


def test_every_voice_gap_is_inside_the_population_it_is_measured_against():
    """`verbs_missing_voice / verb_forms` is only a ratio if the first set is a
    subset of the second."""
    labels = ["pres. ind. 3sg", "aor. act. inf.", "nom. sg. fem.", "-", "interrogative"]
    for label in labels:
        for token_type in ("verb", "noun", "particle", ""):
            morph = parse_morphology(label, token_type)
            assert not missing_voice(morph, token_type) or is_verb_form(morph, token_type)


# --- full forms ------------------------------------------------------------


def test_participle_carries_both_verbal_and_nominal_features():
    morph = parse_morphology("pres. act. part. nom. sg. masc.", "verb")

    assert morph.features() == {
        "tense": "pres",
        "voice": "act",
        "mood": "part",
        "case": "nom",
        "number": "sg",
        "gender": "masc",
    }
    assert morph.is_verbal and morph.is_nominal


def test_infinitive():
    assert features("aor. act. inf.") == {"tense": "aor", "voice": "act", "mood": "inf"}


@pytest.mark.parametrize(
    "label,tense",
    [
        ("pres. act. ind. 3sg", "pres"),
        ("impf. act. ind. 3sg", "impf"),
        ("fut. act. ind. 3sg", "fut"),
        ("aor. act. ind. 3sg", "aor"),
        ("perf. act. ind. 3sg", "perf"),
        ("plup. act. ind. 3sg", "plup"),
    ],
)
def test_every_tense_is_recognised(label, tense):
    assert features(label)["tense"] == tense


def test_person_and_number_are_split_from_a_fused_token():
    assert features("aor. pass. ind. 1pl") == {
        "tense": "aor",
        "voice": "pass",
        "mood": "ind",
        "person": "1",
        "number": "pl",
    }


@pytest.mark.parametrize("person", ["1", "2", "3"])
def test_an_unfused_person_digit_is_read_the_same_as_a_fused_one(person):
    """An earlier prompt asked for bare digits, so these labels are already in
    stored runs. Failing to read them would drop the person *and* demote the
    token out of `ok`, understating coverage on real data."""
    morph = parse_morphology(f"pres. act. ind. {person} sg.")

    assert morph.person == person
    assert morph.number == "sg"
    assert morph.unparsed == []
    assert morph.status is MorphStatus.OK
    assert morph.features() == features(f"pres. act. ind. {person}sg")


def test_participle_mood_is_not_confused_with_the_particle_part_of_speech():
    """"part." is the participle abbreviation; the word "particle" is a part of
    speech. They differ by four letters."""
    assert features("pres. act. part. nom. sg. masc.")["mood"] == "part"
    assert parse_morphology("negative particle").mood is None


def test_bare_part_on_a_particle_is_the_part_of_speech_not_a_participle():
    """Reading it as a mood would invent participles that are not in the text."""
    morph = parse_morphology("part.", "particle")

    assert morph.mood is None
    assert morph.descriptors == ["particle"]
    assert morph.status is MorphStatus.DESCRIPTIVE


def test_bare_part_without_a_type_hint_stays_a_participle():
    assert parse_morphology("part.").mood == "part"


def test_a_real_participle_survives_the_particle_disambiguation():
    """Verbal detail in the label outranks the type hint."""
    morph = parse_morphology("pres. act. part. nom. sg. masc.", "particle")

    assert morph.mood == "part"
    assert morph.descriptors == []
