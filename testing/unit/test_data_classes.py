from infrastructure import InfrastructureMeta


def test_default_not_on_env():
    assert isinstance(InfrastructureMeta().not_on_env, list)