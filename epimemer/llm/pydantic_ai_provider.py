"""Pydantic AI-based LLM provider.

Uses Pydantic AI agents to extract structured outputs (Topics, Facts, Inferences)
validated against our core Pydantic models. Model-agnostic — works with Claude,
OpenAI, Gemini, etc.
"""

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from epimemer.core.types import Fact, Inference, Segment, Topic


# --- Structured output models for Pydantic AI ---
# These are intermediate models that map to our core types.
# Pydantic AI validates LLM output against these before we convert.


class ExtractedTopic(BaseModel):
    """A topic extracted by the LLM."""
    content: str = Field(description="A paragraph-length description of the topic/theme")


class ExtractedFact(BaseModel):
    """A fact extracted by the LLM."""
    content: str = Field(description="An atomic, verifiable, grounded statement")


class ExtractedInference(BaseModel):
    """An inference extracted by the LLM."""
    content: str = Field(description="A higher-level interpretive derivation from the text")


class TopicExtractionResult(BaseModel):
    topics: list[ExtractedTopic]


class FactExtractionResult(BaseModel):
    facts: list[ExtractedFact]


class InferenceExtractionResult(BaseModel):
    inferences: list[ExtractedInference]


class ParentTopicResult(BaseModel):
    content: str = Field(description="A description of the shared theme at a higher level of abstraction")


class EnrichedTopicResult(BaseModel):
    content: str = Field(description="An improved topic description incorporating the associated material")


# --- Agents ---

_TOPIC_SYSTEM_PROMPT = """\
Given a text segment, extract the distinct topics discussed.

For each topic, write a description that captures the theme in enough
detail to distinguish it from related but different topics. A good
description is typically 1 to 5 sentences, but this is not a hard
limit — use as much space as the topic needs to be unambiguous.

Aim for descriptions that are:
- Specific enough to distinguish from sibling topics (e.g., "machine
  learning" vs "reinforcement learning" should have clearly different
  descriptions)
- Dense enough to embed well for similarity search
- Self-contained (understandable without the original text)"""

_FACT_SYSTEM_PROMPT = """\
Given a text segment, extract atomic, verifiable, grounded statements.
Each fact should be:
- Self-contained (understandable without the original context)
- Minimally ambiguous
- Tied to what the text actually states, not what it implies"""

_INFERENCE_SYSTEM_PROMPT = """\
Given a text segment, extract higher-level inferences — things the text
implies or that can be reasoned from the facts presented, but that are
not directly stated. Each inference should be:
- Clearly derived from the content (not pure speculation)
- Distinguished from direct factual statements
- Explicitly provisional (could be wrong)"""

_PARENT_TOPIC_SYSTEM_PROMPT = """\
Given a list of related topic descriptions, write a single parent topic
description that captures their shared theme at a higher level of
abstraction.

The parent should be:
- General enough to encompass all the child topics
- Specific enough to distinguish from unrelated topics
- Typically 1 to 3 sentences, but use as much space as needed

For example, given children about "gradient descent", "backpropagation",
and "learning rate schedules", a good parent might describe "neural
network training optimization techniques" at a level that covers all
three without being so broad it could mean anything."""

_ENRICH_TOPIC_SYSTEM_PROMPT = """\
Given a topic description and associated material (segments, facts) that
have been linked to this topic over time, write an improved description
that better captures the theme.

The improved description should:
- Incorporate key details from the associated material that were missing
  from the original
- Remain a topic description (not a summary of the material itself)
- Be typically 1 to 5 sentences, but use as much space as needed
- Stay self-contained and embed well for similarity search
- Not lose any important nuance from the original description"""

_SPLIT_TOPIC_SYSTEM_PROMPT = """\
Given a topic description and associated material, determine whether
the material actually covers multiple distinct sub-themes. If it does,
extract each sub-theme as a separate topic.

Each subtopic description should:
- Be specific enough to distinguish from its siblings
- Be typically 1 to 5 sentences, but use as much space as needed
- Be self-contained and embed well for similarity search
- Cover a coherent sub-theme, not just an arbitrary partition

If the material is genuinely about one cohesive theme, return an empty
list — do not force a split where none exists."""


class PydanticAIDecompositionProvider:
    """LLM decomposition using Pydantic AI structured outputs."""

    def __init__(self, model: str = "claude-sonnet-4-20250514"):
        self._model = model
        self._topic_agent = Agent(
            model,
            output_type=TopicExtractionResult,
            system_prompt=_TOPIC_SYSTEM_PROMPT,
        )
        self._fact_agent = Agent(
            model,
            output_type=FactExtractionResult,
            system_prompt=_FACT_SYSTEM_PROMPT,
        )
        self._inference_agent = Agent(
            model,
            output_type=InferenceExtractionResult,
            system_prompt=_INFERENCE_SYSTEM_PROMPT,
        )
        self._parent_topic_agent = Agent(
            model,
            output_type=ParentTopicResult,
            system_prompt=_PARENT_TOPIC_SYSTEM_PROMPT,
        )
        self._enrich_topic_agent = Agent(
            model,
            output_type=EnrichedTopicResult,
            system_prompt=_ENRICH_TOPIC_SYSTEM_PROMPT,
        )
        self._split_topic_agent = Agent(
            model,
            output_type=TopicExtractionResult,
            system_prompt=_SPLIT_TOPIC_SYSTEM_PROMPT,
        )

    async def extract_topics(self, segment: Segment) -> list[Topic]:
        result = await self._topic_agent.run(segment.text)
        return [
            Topic(content=t.content, source_id=segment.id, extraction_method=f"llm:{self._model}")
            for t in result.output.topics
        ]

    async def extract_facts(self, segment: Segment) -> list[Fact]:
        result = await self._fact_agent.run(segment.text)
        return [
            Fact(content=f.content, source_id=segment.id, extraction_method=f"llm:{self._model}")
            for f in result.output.facts
        ]

    async def extract_inferences(self, segment: Segment) -> list[Inference]:
        result = await self._inference_agent.run(segment.text)
        return [
            Inference(content=i.content, source_id=segment.id, extraction_method=f"llm:{self._model}")
            for i in result.output.inferences
        ]

    async def synthesize_parent_topic(self, children: list[Topic]) -> Topic:
        child_descriptions = "\n\n".join(
            f"- {child.content}" for child in children
        )
        result = await self._parent_topic_agent.run(
            f"Child topics:\n{child_descriptions}"
        )
        return Topic(
            content=result.output.content,
            source_id=children[0].source_id,
            extraction_method=f"llm:{self._model}:parent_synthesis",
            metadata={"synthesized_from": [c.id for c in children]},
        )

    async def enrich_topic_description(
        self, topic: Topic, associated_material: list[str]
    ) -> str:
        material_text = "\n\n".join(
            f"- {text}" for text in associated_material
        )
        result = await self._enrich_topic_agent.run(
            f"Current topic description:\n{topic.content}\n\n"
            f"Associated material:\n{material_text}"
        )
        return result.output.content

    async def split_topic(
        self, topic: Topic, associated_material: list[str]
    ) -> list[Topic]:
        material_text = "\n\n".join(
            f"- {text}" for text in associated_material
        )
        result = await self._split_topic_agent.run(
            f"Current topic description:\n{topic.content}\n\n"
            f"Associated material:\n{material_text}"
        )
        return [
            Topic(
                content=t.content,
                source_id=topic.source_id,
                extraction_method=f"llm:{self._model}:split",
                metadata={"split_from": topic.id},
            )
            for t in result.output.topics
        ]
