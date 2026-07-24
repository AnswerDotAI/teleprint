"The line editor: text and cursor as pure data, with the readline-emacs subset."
from rich.cells import cell_len

class Buffer:
    "An editable line: `handle` consumes editing keys, returning True when it did."
    def __init__(self, text=''):
        self.text, self.cursor, self.cut = text, len(text), ''
        self.suggestion = ''  # ghost-text tail: shown dim after the cursor, accepted by right/ctrl+e at end

    def insert(self, s):
        self.text = self.text[:self.cursor] + s + self.text[self.cursor:]
        self.cursor += len(s)

    def clear(self):
        self.text, self.cursor = '', 0

    def _back_word(self):
        i = self.cursor
        while i and not self.text[i-1].isalnum(): i -= 1
        while i and self.text[i-1].isalnum(): i -= 1
        return i

    def _fwd_word(self):
        i, n = self.cursor, len(self.text)
        while i < n and not self.text[i].isalnum(): i += 1
        while i < n and self.text[i].isalnum(): i += 1
        return i

    def _cut(self, start, end):
        self.cut = self.text[start:end]
        self.text = self.text[:start] + self.text[end:]
        self.cursor = start

    def handle(self, key):
        "Apply `key` (a `keys.Key`), returning True when it edited or moved."
        k, n = key.name, len(self.text)
        if key.char is not None: self.insert(key.char); return True
        if k in ('right', 'ctrl+f', 'ctrl+e', 'end') and self.cursor == n and self.suggestion:
            self.insert(self.suggestion)
            self.suggestion = ''
            return True
        if k == 'up':
            b = self.text.rfind('\n', 0, self.cursor)
            if b < 0: return False  # top line: the host may treat this as history-back
            col = self.cursor - (b + 1)
            prev_start = self.text.rfind('\n', 0, b) + 1
            self.cursor = min(prev_start + col, b)
            return True
        if k == 'down':
            e = self.text.find('\n', self.cursor)
            if e < 0: return False  # last line: the host may treat this as history-forward
            col = self.cursor - (self.text.rfind('\n', 0, self.cursor) + 1)
            nxt_end = self.text.find('\n', e + 1)
            if nxt_end < 0: nxt_end = len(self.text)
            self.cursor = min(e + 1 + col, nxt_end)
            return True
        if k in ('left','ctrl+b'): self.cursor = max(self.cursor-1, 0)
        elif k in ('right','ctrl+f'): self.cursor = min(self.cursor+1, n)
        elif k in ('home','ctrl+a'): self.cursor = 0
        elif k in ('end','ctrl+e'): self.cursor = n
        elif k == 'alt+b': self.cursor = self._back_word()
        elif k == 'alt+f': self.cursor = self._fwd_word()
        elif k == 'backspace':
            if self.cursor:
                self.text = self.text[:self.cursor-1] + self.text[self.cursor:]
                self.cursor -= 1
        elif k in ('delete','ctrl+d'):
            if self.cursor < n: self.text = self.text[:self.cursor] + self.text[self.cursor+1:]
        elif k == 'ctrl+k': self._cut(self.cursor, n)
        elif k == 'ctrl+u': self._cut(0, self.cursor)
        elif k in ('ctrl+w','alt+backspace'): self._cut(self._back_word(), self.cursor)
        elif k == 'ctrl+y': self.insert(self.cut)
        else: return False
        return True

    def cell_cursor(self, prefix=''):
        "Cell column of the cursor when the text renders after `prefix` (wide chars counted honestly)."
        return cell_len(prefix + self.text[:self.cursor])
