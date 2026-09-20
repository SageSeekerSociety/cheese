"""Two runs sharing a machine must not be handed the same names."""

from tests import isolation


def test_one_slot_keeps_the_names_it_always_had():
    """A laptop and a one-slot machine set no slot, and nothing about them moves."""
    assert isolation.database_names("gw3") == ("cheesex_test_gw3", "cheesex_test_gw3_c")
    assert isolation.database_names("") == ("cheesex_test", "cheesex_test_c")
    assert isolation.redis_database("gw3") == 4
    assert isolation.redis_database("") == 0


def test_two_slots_on_one_machine_share_no_database():
    """The failure this prevents: the harness creates its databases with DROP
    DATABASE ... WITH (FORCE), which disconnects whoever is using one. Two runs
    handed the same name delete each other's data mid-test."""
    first = set(
        isolation.database_names("gw0", "a") + isolation.database_names("gw7", "a")
    )
    second = set(
        isolation.database_names("gw0", "b") + isolation.database_names("gw7", "b")
    )
    assert not (first & second), first & second


def test_two_slots_on_one_machine_share_no_redis_index():
    """Redis keys are scoped by user id, and user ids restart from 1 in every
    database — so a shared index makes one run's account the other run's."""
    block = isolation.REDIS_DATABASES_PER_SLOT
    first = {isolation.redis_database(f"gw{n}", 0) for n in range(block)}
    second = {isolation.redis_database(f"gw{n}", block) for n in range(block)}
    assert not (first & second), first & second
    assert max(first) < block <= min(second)


def test_a_slot_id_cannot_smuggle_anything_into_a_database_name():
    """The slot arrives from the machine's runner configuration, and lands in a
    name this suite interpolates into SQL."""
    name, _client = isolation.database_names("gw0", 'a"; DROP DATABASE x --')
    assert name == "cheesex_test_aDROPDATABASEx_gw0", name
