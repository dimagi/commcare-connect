#!/usr/bin/env python3


# Let's demonstrate how Python resolves decorators
def my_decorator(func):
    print(f"Decorator called with function: {func.__name__}")

    def wrapper(*args, **kwargs):
        print("Wrapper executing before function")
        result = func(*args, **kwargs)
        print("Wrapper executing after function")
        return result

    return wrapper


print("1. Defining the decorated function...")


@my_decorator
def hello(name):
    print(f"Hello, {name}!")
    return f"Greeting for {name}"


print("2. Calling the decorated function...")
result = hello("World")
print(f"3. Result: {result}")
