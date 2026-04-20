from python_pkg.core import Greeter, build_message


def run() -> str:
    greeter = Greeter("World")
    return build_message(greeter.name)
