from agno.tools.toolkit import Toolkit

class CalculatorTools(Toolkit):
    def __init__(self, **kwargs):
        tools = [
            self.add,
            self.subtract,
            self.multiply,
            self.divide,
        ]

        instructions = "Use these tools to perform calculations. Always validate inputs before execution."

        super().__init__(name="calculator_tools", tools=tools, instructions=instructions, add_instructions=True, **kwargs)

    def add(self, a: float, b: float) -> float:
        """Add two numbers and return the result."""
        return a + b

    def subtract(self, a: float, b: float) -> float:
        """Subtract two numbers and return the result."""
        return a - b

    def multiply(self, a: float, b: float) -> float:
        """Multiply two numbers and return the result."""
        return a * b

    def divide(self, a: float, b: float) -> float:
        """Divide two numbers and return the result."""
        return a / b
