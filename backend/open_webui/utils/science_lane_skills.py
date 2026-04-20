from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy.orm import Session

from open_webui.internal.db import get_db_context
from open_webui.models.access_grants import AccessGrant
from open_webui.models.skills import Skill
from open_webui.models.users import User

log = logging.getLogger(__name__)

SCIENCE_LANE_PUBLIC_READ_GRANT = {
    "principal_type": "user",
    "principal_id": "*",
    "permission": "read",
}


@dataclass(frozen=True)
class ScienceLaneSkillSeed:
    id: str
    name: str
    description: str
    content: str
    tags: tuple[str, ...]

    @property
    def meta(self) -> dict[str, Any]:
        return {"tags": list(self.tags)}


SCIENCE_LANE_SKILL_SEEDS: tuple[ScienceLaneSkillSeed, ...] = (
    ScienceLaneSkillSeed(
        id="kdense-scientific-critical-thinking",
        name="K-Dense Scientific Critical Thinking",
        description=(
            "Evaluate scientific claims, study design, evidence quality, bias, "
            "confounding and statistical validity before accepting conclusions."
        ),
        content="""# Scientific Critical Thinking

Ariadne adaptation of the K-Dense scientific-critical-thinking skill.

Use this skill when the user asks whether a scientific claim, paper, experiment, or recommendation actually holds up. Prefer it for criticism, validity checks, evidence grading, bias detection, and methodological sanity checks.

## Ariadne-specific behavior
- Preserve the user's substantive domain terms.
- In Science mode, use the local corpus first when it is thematically compatible.
- If the local corpus is weak, outdated, or off-topic, escalate cleanly to web evidence instead of forcing a corpus answer.
- If a terminal is available, you may use it to create structured notes, comparison tables, or small reproducible calculations. Do not assume any external K-Dense script path exists; if it is not mounted, continue without it.

## Core workflow
1. Restate the exact claim being evaluated.
2. Identify the evidence type.
   Examples: mechanistic study, animal study, observational human study, randomized trial, systematic review, guideline, meta-analysis.
3. Check design validity.
   Ask whether the design can support the conclusion being made.
   Look for controls, randomization, blinding, comparator quality, endpoint quality, preregistration, and missing methodological details.
4. Check bias and confounding.
   Consider selection bias, measurement bias, attrition, publication bias, p-hacking, outcome switching, unmeasured confounding, and weak proxy outcomes.
5. Check the statistics.
   Sample size, power, effect size, uncertainty intervals, multiple comparisons, subgroup fishing, model overfitting, missing data handling, and whether non-significance is being overstated as evidence of no effect.
6. Grade the evidence.
   Distinguish clearly between strong evidence, suggestive evidence, weak evidence, and speculation.
7. Separate what the study shows from what the user wants to do with it.
   Do not smuggle practical recommendations in as if they were directly proven.

## Output contract
Structure the answer in this order when the task is evaluative:
- Bottom-line verdict: 1 short paragraph.
- What supports the claim: concise.
- What weakens the claim: concise.
- Confidence level: high / medium / low with a reason.
- What evidence would change the conclusion.

## Style rules
- Be explicit about uncertainty.
- Prefer concrete flaws over generic skepticism.
- Do not flatten observational evidence into causal language.
- Do not use a hostile peer-review tone unless the user asked for it.
""",
        tags=("science", "kdense", "critical-thinking", "evidence"),
    ),
    ScienceLaneSkillSeed(
        id="kdense-literature-review",
        name="K-Dense Literature Review",
        description=(
            "Plan and execute rigorous literature reviews with scoped questions, "
            "multi-source search, screening, synthesis and explicit evidence gaps."
        ),
        content="""# Literature Review

Ariadne adaptation of the K-Dense literature-review skill.

Use this skill for research overviews, state-of-the-art summaries, gap analyses, scoping reviews, or early-stage systematic-review style work.

## Ariadne-specific behavior
- Start from the local corpus when it plausibly covers the topic.
- Do not force a biomedical framing onto non-biomedical topics. Use PICO only when it genuinely fits; otherwise prefer topic / method / benchmark / timeframe or theory / method / result framing.
- If Ariadne exposes source-native scholarly lookup paths, prefer them before general web search.
  Priority order:
  - Biomedical and life sciences: PubMed, Europe PMC, DOI, Crossref.
  - Cross-disciplinary work: OpenAlex, Crossref, DOI.
  - Field-specific exact identifiers such as DOI, PMID, PMCID, or arXiv id should beat generic keyword search.
- Use general web research only as a fallback for discovery gaps, recency, inaccessible full text, or sources not covered by the source-native paths.
- If the local corpus is clearly off-domain after a light compatibility probe, move on quickly instead of forcing a corpus-heavy answer.
- If a terminal is available, use it for reproducible notes, CSV/Markdown tables, citation lists, or screening ledgers. Do not assume dedicated K-Dense helper scripts are present unless the mounted path is confirmed.

## Planning workflow
1. Define the question precisely.
   If useful, rewrite it as population / intervention / comparator / outcome, or as topic / method / benchmark / timeframe.
2. Set scope.
   Time range, disciplines, inclusion criteria, exclusion criteria, evidence types, and whether preprints are allowed.
3. Build search axes.
   Main concepts, synonyms, abbreviations, competing terminology, and likely high-signal sources.
4. Search in layers.
   - Local corpus first.
   - Then source-native paper and metadata lookup for exact records and identifier resolution.
   - Then web search for academic and primary sources only where source-native lookup is missing, weak, or too narrow.
5. Screen aggressively.
   Remove duplicates, tangential hits, outdated anchors, and low-signal commentary.
6. Synthesize by theme, not one-paper-at-a-time.
   Cluster by mechanism, method, intervention, task family, population, or disagreement pattern.
7. End with evidence gaps and unresolved questions.

## Expected deliverables
For normal review requests, aim to produce:
- Scope and search logic.
- Shortlisted sources with why they matter.
- Thematic synthesis.
- Strongest evidence and main disagreements.
- Clear research gaps.

## Quality rules
- Prefer primary papers, systematic reviews, official reports, or authoritative databases.
- Mark preprints as preprints.
- Distinguish direct evidence from commentary.
- Track recency when the field moves quickly.
- If evidence is sparse, say so early rather than padding the review.
""",
        tags=("science", "kdense", "literature-review", "research"),
    ),
    ScienceLaneSkillSeed(
        id="kdense-peer-review",
        name="K-Dense Peer Review",
        description=(
            "Write structured, constructive manuscript or grant reviews with clear "
            "major issues, methodological critique and publication-risk framing."
        ),
        content="""# Peer Review

Ariadne adaptation of the K-Dense peer-review skill.

Use this skill when the user wants a manuscript review, grant review, section-by-section critique, reviewer response strategy, or journal-style recommendation.

## Ariadne-specific behavior
- Ground major criticisms in the actual text or evidence available.
- If the manuscript references outside claims that matter materially, verify them rather than assuming the references are sound.
- If a terminal is available, use it to build a numbered issue log or revision table.

## Review workflow
1. Summarize the work in 2 to 4 sentences.
2. Decide whether the core question is interesting and whether the presented evidence can answer it.
3. Identify major issues first.
   Focus on design flaws, unsupported claims, missing controls, invalid statistics, reproducibility problems, or reporting gaps.
4. Then identify moderate and minor issues.
   Clarity, organization, figure labeling, citations, terminology, or editorial problems.
5. Separate criticism from fix suggestions.
   Each important issue should ideally include what would be needed to resolve it.
6. End with an editorial-style recommendation.
   Examples: accept with minor revision, major revision, reject, promising but under-supported.

## What counts as a major issue
- Conclusions outrun the evidence.
- Experimental or analytical design cannot support the claim.
- Controls or comparators are missing or weak.
- Statistical treatment is invalid or underreported.
- Reproducibility details are missing.
- Key literature is ignored.

## Output contract
- Summary of the work.
- Major concerns.
- Minor concerns.
- Specific revision requests.
- Recommendation with rationale.

## Tone rules
- Be rigorous but constructive.
- Prefer direct, evidence-linked criticism over vague negativity.
- Do not manufacture balance if the work is genuinely weak.
""",
        tags=("science", "kdense", "peer-review", "manuscript"),
    ),
    ScienceLaneSkillSeed(
        id="kdense-scientific-writing",
        name="K-Dense Scientific Writing",
        description=(
            "Draft or revise scientific prose with evidence-backed structure, "
            "IMRAD discipline and explicit separation of results from interpretation."
        ),
        content="""# Scientific Writing

Ariadne adaptation of the K-Dense scientific-writing skill.

Use this skill for manuscript drafting, section rewrites, abstract polishing, discussion framing, reviewer-response drafting, or turning evidence collections into coherent prose.

## Core principle
Final scientific prose should be written in clear paragraphs, not in outline bullets, unless the user explicitly wants an outline or checklist.

## Ariadne-specific behavior
- Start from evidence, not from style.
- If the user has not yet established the evidence base, pull in literature-review or paper-lookup style work first.
- Use the terminal for file drafting only when available and useful; otherwise compose inline.

## Writing workflow
1. Identify the target artifact.
   Abstract, introduction, methods, results, discussion, rebuttal, review section, grant section.
2. Build a short evidence-backed outline.
   Each section claim should have a source basis or an explicit statement that it is framing or interpretation.
3. Convert the outline into prose.
4. Tighten for scientific style.
   Precision, specificity, short claim chains, explicit uncertainty, and no ornamental filler.
5. Check section discipline.
   - Methods: reproducible and concrete.
   - Results: descriptive, not speculative.
   - Discussion: interpretation, comparison, limitations, implications.
6. Verify every strong claim.
   Unsupported broad claims should be softened or sourced.

## Style rules
- Prefer concrete nouns and verbs over vague abstractions.
- Avoid overselling novelty.
- Separate observed result, inferred mechanism, and practical implication.
- When evidence conflicts, acknowledge the disagreement instead of forcing a smooth narrative.
- If citations are uncertain, flag them before finalizing.
""",
        tags=("science", "kdense", "writing", "imrad"),
    ),
    ScienceLaneSkillSeed(
        id="kdense-paper-lookup",
        name="K-Dense Paper Lookup",
        description=(
            "Find and triage specific papers, identifiers, authors, venues and "
            "authoritative source records before broader synthesis."
        ),
        content="""# Paper Lookup

Ariadne adaptation of the K-Dense paper-lookup skill.

Use this skill when the user needs specific papers, DOIs, author disambiguation, a shortlist of cornerstone sources, or a clean source ledger before larger synthesis.

## Ariadne-specific behavior
- Prefer direct scholarly record systems over generic web search whenever Ariadne exposes them.
  Priority order:
  - Exact identifiers first: DOI, PMID, PMCID, arXiv id.
  - Biomedical and life sciences: PubMed, Europe PMC, DOI, Crossref.
  - Cross-disciplinary work: OpenAlex, Crossref, DOI.
- Use general web search only when exact-record systems fail to resolve the anchor or when you need discovery beyond the current identifier set.
- Do not force biomedical assumptions onto non-biomedical domains. In physics, mathematics, CS, or engineering, title / author / venue / arXiv / DOI anchors are usually stronger than medical-style framing.

## Workflow
1. Extract anchors.
   Keywords, authors, year, title fragments, DOI, PMID, arXiv id, journal, benchmark, method name.
2. Search for exact matches first.
3. Then gather near-matches and likely canonical papers.
4. Deduplicate by DOI, PMID, arXiv id, or title normalization.
5. Rank by relevance and authority.
   Primary paper, strong review, official guideline, benchmark paper, influential replication, or contrary evidence.
6. Return a shortlist with reasons.

## Output contract
For each shortlisted item, include when possible:
- Title
- Authors or first author
- Year
- Venue
- Identifier(s)
- Why it matters
- Confidence that the match is correct

## Rules
- Distinguish exact match from likely match.
- If a citation is incomplete or ambiguous, say that explicitly.
- Prefer authoritative source records over citation-site mirrors.
- Prefer canonical registries and source-native records over generic search-engine snippets.
- If the user is really asking for synthesis rather than lookup, hand off into literature-review mode after the shortlist is stable.
""",
        tags=("science", "kdense", "paper-lookup", "citations"),
    ),
    ScienceLaneSkillSeed(
        id="kdense-citation-management",
        name="K-Dense Citation Management",
        description=(
            "Normalize, verify and format citations, DOIs and bibliography entries "
            "while flagging ambiguous or unverified records."
        ),
        content="""# Citation Management

Ariadne adaptation of the K-Dense citation-management skill.

Use this skill when the user needs bibliography cleanup, DOI verification, reference normalization, missing-metadata repair, or citation-style conversion.

## Ariadne-specific behavior
- Verify against canonical registries first when Ariadne exposes them.
  Priority order:
  - DOI resolver and Crossref for DOI-centered records.
  - PubMed and Europe PMC for biomedical records.
  - OpenAlex for cross-disciplinary metadata cross-checks and citation graph context.
- Use publisher pages or general web search only as a fallback when the canonical record is missing, contradictory, or incomplete.
- Do not invent metadata from citation mirrors, blogs, or scraped reference lists when a canonical source cannot confirm it.
- Do not assume PMID-centered workflows outside biomedicine; for other disciplines, DOI, arXiv id, title, venue, and author matching are often the primary anchors.

## Workflow
1. Extract the citation units.
   Free-text references, DOI list, PMID list, BibTeX-like blocks, URLs, or inline claims needing citation.
2. Normalize metadata.
   Resolve title, authors, year, venue, volume, issue, pages, DOI, PMID, arXiv id.
3. Verify identifiers.
   Prefer DOI, PMID, or canonical source record over secondary citation sites.
4. Flag ambiguity.
   Similar titles, inconsistent years, truncated author lists, broken DOIs, or probable duplicates.
5. Format into the requested style only after the record is stable.

## Output contract
- Verified citations.
- Unverified or ambiguous entries listed separately.
- Duplicates or likely duplicates listed separately.
- If asked for style conversion, provide the converted output plus unresolved warnings.

## Rules
- Never silently invent missing metadata.
- If a citation cannot be verified, keep it marked as provisional.
- Distinguish a missing DOI from a record that likely has no DOI.
- Prefer a short warning over false precision.
- Prefer canonical registry conflicts over generic web conflicts when two records disagree.
""",
        tags=("science", "kdense", "citation-management", "references"),
    ),
)

DEFAULT_SCIENCE_LANE_SKILL_IDS = [seed.id for seed in SCIENCE_LANE_SKILL_SEEDS]


@dataclass
class ScienceLaneSkillSeedReport:
    owner_user_id: Optional[str]
    created_ids: list[str] = field(default_factory=list)
    updated_ids: list[str] = field(default_factory=list)
    activated_ids: list[str] = field(default_factory=list)
    grant_fixed_ids: list[str] = field(default_factory=list)
    skipped_ids: list[str] = field(default_factory=list)

    @property
    def ensured_ids(self) -> list[str]:
        ids = {
            *self.created_ids,
            *self.updated_ids,
            *self.activated_ids,
            *self.grant_fixed_ids,
        }
        return sorted(ids)


def _resolve_science_lane_skill_owner_id(db: Session) -> Optional[str]:
    owner = (
        db.query(User.id)
        .filter(User.role == "admin")
        .order_by(User.created_at.asc())
        .first()
    )
    return owner[0] if owner else None


def _ensure_public_read_grant(db: Session, skill_id: str) -> bool:
    existing = (
        db.query(AccessGrant)
        .filter_by(
            resource_type="skill",
            resource_id=skill_id,
            principal_type=SCIENCE_LANE_PUBLIC_READ_GRANT["principal_type"],
            principal_id=SCIENCE_LANE_PUBLIC_READ_GRANT["principal_id"],
            permission=SCIENCE_LANE_PUBLIC_READ_GRANT["permission"],
        )
        .first()
    )
    if existing is not None:
        return False

    db.add(
        AccessGrant(
            id=str(uuid.uuid4()),
            resource_type="skill",
            resource_id=skill_id,
            principal_type=SCIENCE_LANE_PUBLIC_READ_GRANT["principal_type"],
            principal_id=SCIENCE_LANE_PUBLIC_READ_GRANT["principal_id"],
            permission=SCIENCE_LANE_PUBLIC_READ_GRANT["permission"],
            created_at=int(time.time()),
        )
    )
    return True


def _skill_name_conflicts(db: Session, skill_id: str, name: str) -> bool:
    return (
        db.query(Skill.id)
        .filter(Skill.name == name, Skill.id != skill_id)
        .first()
        is not None
    )


def _ensure_science_lane_skills(db: Session) -> ScienceLaneSkillSeedReport:
    owner_user_id = _resolve_science_lane_skill_owner_id(db)
    report = ScienceLaneSkillSeedReport(owner_user_id=owner_user_id)

    if owner_user_id is None:
        log.info("Skipping Science lane skill seeding because no admin user exists yet.")
        return report

    for seed in SCIENCE_LANE_SKILL_SEEDS:
        now = int(time.time())
        skill = db.query(Skill).filter_by(id=seed.id).first()

        if skill is None and _skill_name_conflicts(db, seed.id, seed.name):
            log.warning(
                "Skipping Science lane skill seed for %s because name '%s' is already in use.",
                seed.id,
                seed.name,
            )
            report.skipped_ids.append(seed.id)
            continue

        if skill is None:
            skill = Skill(
                id=seed.id,
                user_id=owner_user_id,
                name=seed.name,
                description=seed.description,
                content=seed.content,
                meta=seed.meta,
                is_active=True,
                updated_at=now,
                created_at=now,
            )
            db.add(skill)
            report.created_ids.append(seed.id)
        else:
            changed = False

            if skill.name != seed.name and not _skill_name_conflicts(db, seed.id, seed.name):
                skill.name = seed.name
                changed = True
            if skill.description != seed.description:
                skill.description = seed.description
                changed = True
            if skill.content != seed.content:
                skill.content = seed.content
                changed = True
            if skill.meta != seed.meta:
                skill.meta = seed.meta
                changed = True
            if not skill.user_id:
                skill.user_id = owner_user_id
                changed = True
            if not skill.is_active:
                skill.is_active = True
                report.activated_ids.append(seed.id)
                changed = True

            if changed:
                skill.updated_at = now
                report.updated_ids.append(seed.id)

        if _ensure_public_read_grant(db, seed.id):
            report.grant_fixed_ids.append(seed.id)

    db.commit()
    return report


def ensure_science_lane_skills(
    db: Optional[Session] = None,
) -> ScienceLaneSkillSeedReport:
    if db is not None:
        return _ensure_science_lane_skills(db)

    with get_db_context() as session:
        return _ensure_science_lane_skills(session)
