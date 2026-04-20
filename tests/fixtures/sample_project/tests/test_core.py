from python_pkg.core import Greeter


def test_greet() -> None:
    greeter = Greeter("alice")
    assert greeter.greet() == "Hello, Alice"
