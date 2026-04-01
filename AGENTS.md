Prefer a functional programming style.
Minimise the use of inheritence.
Use type annotations in Python where possible but don't overcomplicate the code in an attempt to have perfect type hints.
Avoid making classes that use `self` or `@staticmethod`.
However, using Pydantic BaseModel for data structures is encouraged.
Prefer uv over pip.
Use the Petritype library `../petritype` for complex precesses and data pipelines.
Remember to run `uv lock --upgrade-package petritype` in case Petritype main branch has been updated.
When using Marimo notebooks remember to not re-define variables in different cells, that cells correspond to functions and these funcitons need to return values.

Our goal is to build a robust and secure system, not simply a prototype. We don't want to trade speed for technical debt.

# Frontend Coding Style
1. Prefer a functional programming style.
2. Prefer Typescript over plain Javascript.
3. Use Tailwind CSS.


