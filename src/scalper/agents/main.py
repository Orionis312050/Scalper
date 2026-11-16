"""Scalper multi-agents (Mistral Agents SDK 1.1).

Architecture : un orchestrateur délègue à des subagents (chacun sa propre
session), comme les subagents de Claude Code côté Mistral Studio.

Lancement : pip install -e ".[agents]" puis `agents chat` (entrypoint déclaré
dans pyproject.toml : scalper.agents.main:agent).
"""

from __future__ import annotations

from mistralai import agents

from .tools import check_source, qa_translation, run_chapter_pipeline, translate_text

scraper_agent = agents.Agent(
    name="scraper-agent",
    model="mistral-small-latest",
    instructions=(
        "Tu es l'agent scraper de Scalper. Tu valides toujours la source avec "
        "check_source avant toute requête (aucune source non autorisée, ex. sites "
        "de scanlation : robots.txt, rate limiting, pas de contournement). Tu "
        "exécutes ensuite le scraping via run_chapter_pipeline ou list/fetch."
    ),
    tools=[check_source, run_chapter_pipeline],
)

translator_agent = agents.Agent(
    name="translator-agent",
    model="mistral-large-latest",
    instructions=(
        "Tu es l'agent traducteur de Scalper. Tu traduis avec translate_text en "
        "respectant strictement le glossaire (noms propres, termes récurrents), tu "
        "ne traduis jamais les placeholders [IMG:n], et tu fais valider le résultat "
        "par qa_translation (score >= 80 requis)."
    ),
    tools=[translate_text, qa_translation],
)

qa_agent = agents.Agent(
    name="qa-agent",
    model="mistral-small-latest",
    instructions=(
        "Tu es l'agent QA de Scalper. Tu compares source et traduction avec "
        "qa_translation, tu signales toute incohérence de glossaire, bloc vide, "
        "placeholder altéré ou mauvaise langue, et tu rends un verdict pass/fail."
    ),
    tools=[qa_translation],
)

agent = agents.Agent(
    name="scalper-orchestrator",
    model="mistral-large-latest",
    instructions=(
        "Tu es l'orchestrateur du pipeline Scalper (scraping légal + traduction de "
        "chapitres manga/webtoon). Règle absolue : seules les sources légales sont "
        "traitées (API officielles, Creative Commons, domaine public, contenus "
        "propres) ; check_source doit retourner allowed avant toute action. "
        "Délègue le scraping à scraper-agent, la traduction à translator-agent et "
        "le contrôle qualité à qa-agent, puis consolide les résultats."
    ),
    tools=[check_source, run_chapter_pipeline],
    subagents=[scraper_agent, translator_agent, qa_agent],
)
