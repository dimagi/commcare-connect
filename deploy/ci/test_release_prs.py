from release_prs import collect_pr_numbers


def pr(number, merged=True, base="main"):
    return {"number": number, "merged_at": "2026-10-26T10:00:00Z" if merged else None, "base": {"ref": base}}


def test_collect_pr_numbers_keeps_prs_merged_into_main_once_each():
    commit_pulls = [
        [pr(12)],
        [pr(12)],  # several commits from the same PR
        [pr(7), pr(30, merged=False)],  # an open PR that also contains the commit
        [pr(9, base="ui-main")],  # merged into another branch
        [],  # a direct push with no PR
    ]

    assert collect_pr_numbers(commit_pulls) == [7, 12]
