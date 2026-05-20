# Welcome to MDLook

MDLook is a fast, portable, fully offline Markdown editor for Windows. This file is a quick tour of what it can do.

---

## Text Formatting

You can write **bold**, *italic*, ***bold italic***, ~~strikethrough~~, ++underline++, `inline code`, and ==highlighted== text.

Colored text with adaptive tokens: {color:orange}orange{/color}, {color:purple}purple{/color}, {color:cyan}cyan{/color}.

## Code

```python
def greet(name: str) -> str:
    """Return a friendly greeting."""
    return f"Hello, {name}! Welcome to MDLook."

print(greet("World"))
```

## Math

Euler's identity: $e^{i\pi} + 1 = 0$

The quadratic formula:

$$
x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}
$$

## Diagrams

```mermaid
graph LR
    A[Open .md file] --> B[Edit]
    B --> C[Live Preview]
    C --> D[Save to disk]
    D --> E[Done]
```

## Tables

| Feature | MDLook |
|---------|--------|
| Portable | Yes — unzip and run |
| Offline | 100% — no internet needed |
| Math (KaTeX) | Built-in |
| Diagrams (Mermaid) | Built-in |
| System tray | Yes |

## Lists

- [x] Write Markdown
- [x] See it rendered instantly
- [ ] Tell a friend about MDLook

## Blockquote

> "Simplicity is the ultimate sophistication."
> — Leonardo da Vinci

---

See **GUIDE.md** for the full feature reference. Press `E` to edit this file, `Ctrl+S` to save.
