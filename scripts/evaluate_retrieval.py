"""
Retrieval evaluation script for RideWise Assist.

Runs a set of ground-truth questions against the retrieval system and
measures Recall@K and MRR for policy questions.

Usage:
    cd ridewise-assist
    python scripts/evaluate_retrieval.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Ground-truth evaluation set
# Format: (question, expected_source_file, expected_page, expected_keywords)
EVAL_SET = [
    # ── Direct policy questions ───────────────────────────────────────────────
    (
        "What is the cancellation refund if I cancel 30 hours before pickup?",
        "02_Cancellation_and_Refund_Policy_v1.pdf", 1, ["100%", "24 hours"],
    ),
    (
        "What refund do I get for cancelling 10 hours before pickup under v1?",
        "02_Cancellation_and_Refund_Policy_v1.pdf", 1, ["50%", "6 hours"],
    ),
    (
        "Is there any refund if I cancel 1 hour before pickup under policy v1?",
        "02_Cancellation_and_Refund_Policy_v1.pdf", 1, ["No refund", "0%"],
    ),
    # ── Policy v2 (from scanned annexure) ────────────────────────────────────
    (
        "What is the refund if I cancel 50 hours before pickup under version 2?",
        "03_Cancellation_and_Refund_Policy_v2.pdf", 2, ["100%", "48 hours"],
    ),
    (
        "What refund applies if I cancel 20 hours before pickup under v2?",
        "03_Cancellation_and_Refund_Policy_v2.pdf", 2, ["75%", "12 hours"],
    ),
    (
        "What is the v2 refund for cancelling 5 hours before pickup?",
        "03_Cancellation_and_Refund_Policy_v2.pdf", 2, ["25%", "2 hours"],
    ),
    (
        "Is there a refund if I cancel 1 hour before pickup under policy v2?",
        "03_Cancellation_and_Refund_Policy_v2.pdf", 2, ["No refund", "0%"],
    ),
    # ── Policy version selection ──────────────────────────────────────────────
    (
        "Which policy version applies to my booking?",
        "02_Cancellation_and_Refund_Policy_v1.pdf", 1, ["booked", "July 2026"],
    ),
    (
        "When did version 2 of the cancellation policy become effective?",
        "03_Cancellation_and_Refund_Policy_v2.pdf", 1, ["1 July 2026"],
    ),
    # ── Late return fees ──────────────────────────────────────────────────────
    (
        "How is the late return fee calculated?",
        "04_Late_Return_and_Damage_Penalty_Schedule.pdf", 1, ["1.5", "hourly rate", "part"],
    ),
    (
        "What is the late fee for an Ather 450X?",
        "04_Late_Return_and_Damage_Penalty_Schedule.pdf", 1, ["120", "180"],
    ),
    (
        "What grace period does a GOLD tier customer get for late returns?",
        "04_Late_Return_and_Damage_Penalty_Schedule.pdf", 3, ["30 minutes", "GOLD"],
    ),
    (
        "Do SILVER customers get a grace period on late returns?",
        "04_Late_Return_and_Damage_Penalty_Schedule.pdf", 3, ["SILVER", "None"],
    ),
    # ── EV battery penalty ────────────────────────────────────────────────────
    (
        "What happens if I return an EV with 5% battery?",
        "04_Late_Return_and_Damage_Penalty_Schedule.pdf", 2, ["150", "10%"],
    ),
    (
        "Is the low battery fee the same as a late return fee?",
        "05_EV_User_Guide_and_FAQ.pdf", 2, ["separate", "both"],
    ),
    (
        "What is the EV battery level required at return?",
        "04_Late_Return_and_Damage_Penalty_Schedule.pdf", 2, ["10%"],
    ),
    # ── Damage claims ─────────────────────────────────────────────────────────
    (
        "What does claim status CHARGED mean?",
        "04_Late_Return_and_Damage_Penalty_Schedule.pdf", 2, ["CHARGED", "assessed"],
    ),
    (
        "What does DISPUTED damage claim status mean?",
        "04_Late_Return_and_Damage_Penalty_Schedule.pdf", 2, ["DISPUTED", "review"],
    ),
    # ── Rental terms ─────────────────────────────────────────────────────────
    (
        "What is the security deposit for a bike rental?",
        "01_Rental_Agreement_and_Terms.pdf", 2, ["1,000", "Bike"],
    ),
    (
        "What is the GST rate on rental charges?",
        "01_Rental_Agreement_and_Terms.pdf", 2, ["18%"],
    ),
    (
        "What happens if I don't show up for my booking?",
        "01_Rental_Agreement_and_Terms.pdf", 2, ["no-show", "no refund"],
    ),
    # ── EV guide ─────────────────────────────────────────────────────────────
    (
        "What battery level do EVs have at pickup?",
        "05_EV_User_Guide_and_FAQ.pdf", 1, ["80%"],
    ),
    (
        "Can I charge an EV at home?",
        "05_EV_User_Guide_and_FAQ.pdf", 2, ["home", "charging cable"],
    ),
]


def evaluate():
    from src.ridewise.retrieval.hybrid import load_retrieval_system, retrieve

    print("Loading retrieval system...")
    load_retrieval_system()

    hits_at_1 = 0
    hits_at_3 = 0
    hits_at_5 = 0
    mrr_sum = 0.0
    keyword_recall_sum = 0.0
    total = len(EVAL_SET)

    print(f"\nRunning {total} evaluation queries...\n")
    print(f"{'Question':<55} {'Hit@1':>5} {'Hit@5':>5} {'Rank':>5}")
    print("-" * 75)

    for question, exp_file, exp_page, exp_keywords in EVAL_SET:
        results = retrieve(question, top_k=5)

        rank = None
        keyword_hit = False

        for i, chunk in enumerate(results, start=1):
            # Check file match
            file_match = chunk["source_file"] == exp_file
            # Check page proximity (±1 page tolerance for multi-page sections)
            page_match = abs(chunk["page_number"] - exp_page) <= 1

            if file_match and page_match:
                if rank is None:
                    rank = i

            # Check keyword presence in any top-5 result
            combined_text = " ".join(r["text"] for r in results).lower()
            if all(kw.lower() in combined_text for kw in exp_keywords):
                keyword_hit = True

        if rank is not None:
            mrr_sum += 1.0 / rank
            if rank == 1:
                hits_at_1 += 1
            if rank <= 3:
                hits_at_3 += 1
            if rank <= 5:
                hits_at_5 += 1

        if keyword_hit:
            keyword_recall_sum += 1

        rank_str = str(rank) if rank else "MISS"
        h1 = "✓" if rank == 1 else " "
        h5 = "✓" if rank and rank <= 5 else " "
        print(f"{question[:54]:<55} {h1:>5} {h5:>5} {rank_str:>5}")

    print("\n" + "=" * 75)
    print(f"Evaluation complete — {total} questions")
    print(f"  Recall@1 : {hits_at_1/total:.2%}  ({hits_at_1}/{total})")
    print(f"  Recall@3 : {hits_at_3/total:.2%}  ({hits_at_3}/{total})")
    print(f"  Recall@5 : {hits_at_5/total:.2%}  ({hits_at_5}/{total})")
    print(f"  MRR      : {mrr_sum/total:.3f}")
    print(f"  Keyword recall@5: {keyword_recall_sum/total:.2%}  ({int(keyword_recall_sum)}/{total})")


if __name__ == "__main__":
    evaluate()
