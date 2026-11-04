import csv

import pytest

from clipper import evaluation


def test_average_ranks_share_the_rank_between_ties():
    assert evaluation.average_ranks([10, 20, 20, 30]) == [1, 2.5, 2.5, 4]
    assert evaluation.average_ranks([5, 5, 5]) == [2, 2, 2]
    assert evaluation.average_ranks([3, 1, 2]) == [3, 1, 2]


def test_spearman_matches_a_published_worked_example():
    # Wikipedia's Spearman example (IQ vs hours of TV per week): rho = -0.175757...
    iq = [86, 97, 99, 100, 101, 103, 106, 110, 112, 113]
    tv = [2, 20, 28, 27, 50, 29, 7, 17, 6, 12]
    assert evaluation.spearman(iq, tv) == pytest.approx(-0.175757, abs=1e-5)


def test_spearman_with_ties_matches_a_hand_calculation():
    # ranks x = [1, 2.5, 2.5, 4], y = [1, 3, 2, 4]  ->  4.5 / sqrt(4.5 * 5) = 0.948683...
    assert evaluation.spearman([1, 2, 2, 3], [1, 3, 2, 4]) == pytest.approx(0.948683, abs=1e-5)


def test_spearman_extremes():
    assert evaluation.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert evaluation.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    assert evaluation.spearman([1, 2, 3, 4], [1, 20, 3, 40]) == pytest.approx(0.8)


def test_spearman_is_undefined_rather_than_wrong_for_tiny_or_flat_data():
    assert evaluation.spearman([1, 2], [1, 2]) is None
    assert evaluation.spearman([5, 5, 5, 5], [1, 2, 3, 4]) is None
    assert evaluation.spearman([1, 2, 3], [1, 2]) is None


@pytest.mark.parametrize("text,expected", [("8", 8.0), ("8.5", 8.5), (" 7/10", 7.0), ("", None), (None, None), ("n/a", None)])
def test_parse_number(text, expected):
    assert evaluation.parse_number(text) == expected


def _write_csv(path, rows, columns=("title", "category", "virality_score", "my_rating")):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def test_load_rated_keeps_only_rows_with_both_numbers(tmp_path):
    path = tmp_path / "review.csv"
    _write_csv(
        path,
        [
            {"title": "a", "category": "drama", "virality_score": "9", "my_rating": "8/10"},
            {"title": "b", "category": "funny", "virality_score": "7", "my_rating": ""},  # not rated by you
            {"title": "c", "category": "wtf", "virality_score": "n/a", "my_rating": "5"},  # no model score
            {"title": "é", "category": "", "virality_score": "5", "my_rating": "4"},
        ],
    )
    rated = evaluation.load_rated(path)
    assert [(r.title, r.model_score, r.my_rating) for r in rated] == [("a", 9.0, 8.0), ("é", 5.0, 4.0)]


def test_load_rated_explains_a_wrong_file(tmp_path):
    path = tmp_path / "other.csv"
    _write_csv(path, [{"title": "x"}], columns=("title",))
    with pytest.raises(ValueError, match="my_rating.*virality_score|virality_score.*my_rating"):
        evaluation.load_rated(path)


def _rated(pairs, category="drama"):
    return [evaluation.Rated(f"c{i}", category, m, y) for i, (m, y) in enumerate(pairs)]


def test_evaluate_against_a_hand_computed_example():
    # model 9 8 7 6 5 4 vs mine 9 7 8 4 5 2: sum d^2 = 4, rho = 1 - 6*4/(6*35) = 0.885714
    rated = _rated([(9, 9), (8, 7), (7, 8), (6, 4), (5, 5), (4, 2)])
    report = evaluation.evaluate(rated, good=7, k=3)

    assert report.n == 6
    assert report.spearman == pytest.approx(0.885714, abs=1e-5)
    assert report.precision_at_k == 1.0  # its top 3 are the three clips I rated >= 7
    assert report.base_rate == 0.5
    assert report.lift == 2.0
    assert report.mean_model_good == 8.0 and report.mean_model_other == 5.0


def test_evaluate_when_the_model_is_worse_than_chance():
    rated = _rated([(9, 2), (8, 3), (7, 4), (6, 8), (5, 9), (4, 10)])
    report = evaluation.evaluate(rated, good=7, k=3)
    assert report.spearman == pytest.approx(-1.0)
    assert report.precision_at_k == 0.0


def test_k_is_capped_at_the_number_of_rated_clips():
    report = evaluation.evaluate(_rated([(9, 9), (5, 5)]), k=10)
    assert report.k == 2


def test_by_category_groups_and_orders_by_size():
    rated = _rated([(9, 8), (8, 9)], "drama") + _rated([(5, 4)], "funny")
    rows = evaluation.evaluate(rated).by_category
    assert [r[0] for r in rows] == ["drama", "funny"]
    assert rows[0][1:] == (2, 8.5, 8.5)


def test_report_text_is_honest_about_small_samples(capsys):
    text = evaluation.format_report(evaluation.evaluate(_rated([(9, 9), (8, 7), (7, 8), (6, 4), (5, 5), (4, 2)])))
    assert "Rated clips: 6" in text
    assert "Spearman" in text and "0.89" in text
    assert "Only 6 rated clips" in text  # fewer than 20


def test_a_large_enough_sample_gets_no_warning():
    rated = _rated([(i % 10 + 1, (i * 3) % 10 + 1) for i in range(25)])
    assert "noisy" not in evaluation.format_report(evaluation.evaluate(rated))


def test_empty_report_tells_you_what_to_do():
    assert "my_rating" in evaluation.format_report(evaluation.evaluate([]))
