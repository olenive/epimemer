"""One metacontext building on another: writing the `based_on` edge.

A role-playing world can follow real history up to a point and part from it
there. The edge records that, from the derived metacontext to its base, with
`diverges` saying where they part. These tests cover what writes it and what is
refused: one base per metacontext, no metacontext built on itself, no cycle, and
a divergence that is a date or a named moment.

Every backend, through the `storage` fixture.
"""

import pytest

from epimemer.core.types import BASE_METACONTEXT_ID, EdgeType
from epimemer.mcp import tools

IN_1990 = {"instant_kind": "precise", "at": "1990-01-01"}
LATE_EIGHTIES = {"instant_kind": "named", "label": "the late 1980s"}


async def _bases_of(storage, metacontext_id: str) -> list[str]:
    edges = await storage.get_edges_from(metacontext_id, edge_type=EdgeType.BASED_ON)
    return [edge.dst_id for edge in edges]


async def _night_city(storage, **base) -> dict:
    result, _ = await tools.create_metacontext(
        "Night City", storage, metacontext_id="night-city", **base
    )
    return result


class TestCreatingWithABase:
    async def test_the_base_and_its_divergence_are_recorded(self, storage):
        result = await _night_city(storage, based_on=BASE_METACONTEXT_ID, diverges=IN_1990)

        based_on = result["based_on"]
        assert based_on["metacontext_id"] == BASE_METACONTEXT_ID
        assert based_on["content"] == "The Real"
        assert based_on["diverges"]["instant_kind"] == "precise"
        assert based_on["diverges"]["at"].startswith("1990-01-01")
        (edge,) = await storage.get_edges_from("night-city", edge_type=EdgeType.BASED_ON)
        assert edge.dst_id == BASE_METACONTEXT_ID
        assert edge.metadata["diverges"] == based_on["diverges"]

    async def test_a_named_divergence_keeps_its_words(self, storage):
        result = await _night_city(storage, based_on=BASE_METACONTEXT_ID, diverges=LATE_EIGHTIES)

        assert result["based_on"]["diverges"] == LATE_EIGHTIES

    async def test_a_metacontext_with_no_base_says_so(self, storage):
        result = await _night_city(storage)

        assert result["based_on"] is None
        assert await _bases_of(storage, "night-city") == []

    async def test_a_base_that_does_not_exist_is_refused_and_the_refusal_lists_what_does(
        self, storage
    ):
        with pytest.raises(ValueError, match="does not exist in graph") as refusal:
            await _night_city(storage, based_on="the-reel")

        assert f"'{BASE_METACONTEXT_ID}' (The Real)" in str(refusal.value)
        assert await storage.get_metacontext("night-city") is None

    async def test_a_divergence_without_a_base_is_refused(self, storage):
        with pytest.raises(ValueError, match="without a base"):
            await _night_city(storage, diverges=IN_1990)

        assert await storage.get_metacontext("night-city") is None

    @pytest.mark.parametrize("kind", ["unknown", "unbounded"])
    async def test_a_divergence_that_is_neither_a_date_nor_a_name_is_refused(self, storage, kind):
        with pytest.raises(ValueError, match=f'"{kind}"'):
            await _night_city(
                storage, based_on=BASE_METACONTEXT_ID, diverges={"instant_kind": kind}
            )

        assert await storage.get_metacontext("night-city") is None

    async def test_a_divergence_that_is_not_an_instant_is_refused(self, storage):
        with pytest.raises(ValueError, match="not an instant"):
            await _night_city(storage, based_on=BASE_METACONTEXT_ID, diverges={"year": 1990})

    async def test_recreating_keeps_the_base_unless_one_is_passed(self, storage):
        await _night_city(storage, based_on=BASE_METACONTEXT_ID, diverges=IN_1990)

        result, _ = await tools.create_metacontext(
            "Night City, revised", storage, metacontext_id="night-city"
        )

        assert result["based_on"]["metacontext_id"] == BASE_METACONTEXT_ID
        assert await _bases_of(storage, "night-city") == [BASE_METACONTEXT_ID]

    async def test_recreating_with_a_base_replaces_it(self, storage):
        await tools.create_metacontext("Earth Two", storage, metacontext_id="earth-two")
        await _night_city(storage, based_on=BASE_METACONTEXT_ID)

        result = await _night_city(storage, based_on="earth-two")

        assert result["based_on"]["metacontext_id"] == "earth-two"
        assert await _bases_of(storage, "night-city") == ["earth-two"]

    async def test_a_metacontext_based_on_itself_is_refused(self, storage):
        await _night_city(storage)

        with pytest.raises(ValueError, match="based on itself"):
            await _night_city(storage, based_on="night-city")

        assert await _bases_of(storage, "night-city") == []

    async def test_a_cycle_is_refused(self, storage):
        """A based on B, B based on C: C based on A would close the loop."""
        await tools.create_metacontext("C", storage, metacontext_id="c")
        await tools.create_metacontext("B", storage, metacontext_id="b", based_on="c")
        await tools.create_metacontext("A", storage, metacontext_id="a", based_on="b")

        with pytest.raises(ValueError, match="cycle") as refusal:
            await tools.set_metacontext_base("c", storage, base_id="a")

        assert "a -> b -> c" in str(refusal.value)
        assert await _bases_of(storage, "c") == []


class TestSettingABase:
    async def test_set(self, storage):
        await _night_city(storage)

        result, meta = await tools.set_metacontext_base(
            "night-city", storage, base_id=BASE_METACONTEXT_ID, diverges=LATE_EIGHTIES
        )

        assert result == {
            "metacontext_id": "night-city",
            "based_on": {
                "metacontext_id": BASE_METACONTEXT_ID,
                "content": "The Real",
                "diverges": LATE_EIGHTIES,
            },
            "replaced": None,
        }
        assert meta.retrieved == []

    async def test_replace_leaves_one_edge_and_names_the_old_base(self, storage):
        await tools.create_metacontext("Earth Two", storage, metacontext_id="earth-two")
        await _night_city(storage, based_on=BASE_METACONTEXT_ID, diverges=IN_1990)

        result, _ = await tools.set_metacontext_base("night-city", storage, base_id="earth-two")

        assert result["replaced"] == BASE_METACONTEXT_ID
        assert result["based_on"]["metacontext_id"] == "earth-two"
        assert result["based_on"]["diverges"] is None
        assert await _bases_of(storage, "night-city") == ["earth-two"]

    async def test_withdraw(self, storage):
        await _night_city(storage, based_on=BASE_METACONTEXT_ID, diverges=IN_1990)

        result, _ = await tools.set_metacontext_base("night-city", storage)

        assert result == {
            "metacontext_id": "night-city",
            "based_on": None,
            "replaced": BASE_METACONTEXT_ID,
        }
        assert await _bases_of(storage, "night-city") == []

    async def test_a_metacontext_that_does_not_exist_is_refused(self, storage):
        with pytest.raises(ValueError, match="does not exist in graph"):
            await tools.set_metacontext_base("night-city", storage, base_id=BASE_METACONTEXT_ID)

        assert await _bases_of(storage, "night-city") == []

    async def test_a_refused_change_leaves_the_base_in_place(self, storage):
        await _night_city(storage, based_on=BASE_METACONTEXT_ID, diverges=IN_1990)

        with pytest.raises(ValueError, match="without a base"):
            await tools.set_metacontext_base("night-city", storage, diverges=LATE_EIGHTIES)
        with pytest.raises(ValueError, match="based on itself"):
            await tools.set_metacontext_base("night-city", storage, base_id="night-city")

        assert await _bases_of(storage, "night-city") == [BASE_METACONTEXT_ID]
