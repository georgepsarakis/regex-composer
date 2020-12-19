from collections import deque
import math
import re

from regex_composer.exceptions import SampleNotMatchedError, \
    InvalidRangeError
from regex_composer.flag import FlagSet


class Operator:
    def __copy__(self):
        return self.__class__()

    def __repr__(self):
        return f'\'{self.__class__.__name__} -> {str(self)}\''


class OpeningBracket(Operator):
    def __str__(self):
        return '['


class ClosingBracket(Operator):
    def __str__(self):
        return ']'


class Expression:
    def __init__(self, parent: 'Expression' = None):
        self._token_stack = deque()
        self._bracket_stack = []
        if parent:
            self._copy_state(parent)

    def _copy_state(self, other, clear=True):
        if clear:
            self.bracket_stack.clear()
            self.token_stack.clear()
        self.bracket_stack.extend(other.bracket_stack)
        self.token_stack.extend(other.token_stack)

    @property
    def token_stack(self) -> deque:
        return self._token_stack

    @property
    def bracket_stack(self) -> list:
        return self._bracket_stack

    def has_open_range(self):
        if not self.bracket_stack:
            return False
        return isinstance(self.bracket_stack[-1], OpeningBracket)

    def end_range(self):
        if not self.has_open_range():
            return self

        if self.bracket_stack:
            last_item_in_stack = self.bracket_stack[-1]
        else:
            last_item_in_stack = None

        if not isinstance(last_item_in_stack, OpeningBracket):
            raise RuntimeError('Cannot close bracket without opening')

        return self.clone_with_updates(append=ClosingBracket())

    def _prepare_for_build(self):
        return self.end_range()

    def build(self):
        return ''.join(map(str, self._prepare_for_build().token_stack))

    def __repr__(self):
        return f"{self.__class__.__name__}<{hex(id(self))}>[{self.token_stack}]"

    def __str__(self):
        return f'Expression: /{self.build()}/'

    def __add__(self, other):
        new = self.__class__(parent=self)
        new._copy_state(other, clear=False)
        return new

    def clone(self) -> 'Expression':
        new = self.__class__(parent=self)
        self._on_after_clone(new)
        return new

    def _on_after_clone(self, new):
        pass

    def clone_with_updates(self, append=None, prepend=None) -> 'Expression':
        if append is not None and not isinstance(append, (list, tuple)):
            append = (append,)

        if prepend is not None and not isinstance(prepend, (list, tuple)):
            prepend = (prepend,)

        clone = self.clone()
        clone.token_stack.extendleft(reversed(prepend or []))
        clone.token_stack.extend(append or [])

        if append:
            for item in append:
                if isinstance(item, ClosingBracket):
                    if clone.has_open_range():
                        clone.bracket_stack.pop()
                elif isinstance(item, OpeningBracket):
                    clone.bracket_stack.append(item)

        return clone


class Pattern(Expression):
    def __init__(self, *args, **kwargs):
        super(Pattern, self).__init__(*args, **kwargs)
        self._flags = None

    def __eq__(self, other):
        return self.flags.equals(other.flags) and \
               self.token_stack == other.token_stack

    @property
    def flags(self):
        if self._flags is None:
            self._flags = FlagSet(pattern=self)
        return self._flags

    def _on_after_clone(self, new):
        new._flags = FlagSet.copy(pattern=new)

    def group(self, name=None):
        # TODO: all open ranges should be closed before applying group
        if name is None:
            return Group(self)()
        else:
            return NamedGroup(self)(name)

    def whitespace(self, match=True) -> 'Pattern':
        return Whitespace(self)(match)

    def lowercase_ascii_letters(self, **kwargs):
        return AsciiLetterCharacter(self)(lowercase=True, **kwargs)
    # Alias due to high frequency use (along with case-insensitive flag)
    ascii_letters = lowercase_ascii_letters

    def uppercase_ascii_letters(self, **kwargs) -> 'Pattern':
        return AsciiLetterCharacter(self)(lowercase=False, **kwargs)

    def any_number_between(self, **kwargs):
        return Number(self)(**kwargs)

    def quantify(self, minimum=0, maximum=math.inf):
        addition = None
        if minimum == 0 and math.isinf(maximum):
            addition = '*'
        elif minimum == 1 and math.isinf(maximum):
            addition = '+'
        elif minimum == maximum:
            addition = f'{{{minimum}}}'
        elif minimum > 1 and math.isinf(maximum):
            addition = f'{{{minimum},}}'
        elif not math.isinf(maximum):
            addition = f'{{{minimum},{maximum}}}'
        return self.end_range().clone_with_updates(append=addition)

    def wildcard(self):
        return self.clone_with_updates('.')

    def literal(self, string):
        return Literal(self)(string)

    def start_anchor(self):
        return self.clone_with_updates(append='^')

    def end_anchor(self):
        return self.clone_with_updates(append='$')

    def compile(self):
        return re.compile(self.build(), self.flags.compile())

    def test(self, sample):
        regex = self.compile()
        match = regex.match(sample)
        if not match:
            raise SampleNotMatchedError(f'{regex} tested with "{sample}"')
        return match

    def case_insensitive(self, enabled=True):
        return self.clone().flags.case_insensitive(enabled=enabled)

    def multiline(self, enabled=True):
        return self.clone().flags.multiline(enabled=enabled)

    def dot_matches_newline(self, enabled=True):
        return self.clone().flags.dot_matches_newline(enabled=enabled)

    def ascii_only(self, enabled=True):
        return self.clone().flags.ascii_only(enabled=enabled)

    def __str__(self):
        initial = super(Pattern, self).__str__()
        return f'{initial}{self.flags}'


class Group(Pattern):
    def __call__(self) -> 'Pattern':
        return self.end_range().clone_with_updates(
            prepend='(',
            append=')'
        )


class NamedGroup(Group):
    def __call__(self, name) -> 'Pattern':
        return self.end_range().clone_with_updates(
            prepend=('(', f'?P<{name}>'),
            append=')'
        )


class Literal(Pattern):
    def __call__(self, string):
        return self.clone_with_updates(re.escape(string))


class Whitespace(Pattern):
    def __call__(self, match):
        return self.clone_with_updates('\\s' if match else '\\S')


class Range(Pattern):
    def __call__(self, start, end, closed=False, negated=False):
        if negated:
            start = f'^{start}'

        additions = []
        if not self.has_open_range():
            additions.append(OpeningBracket())
        additions.append(f'{start}-{end}')
        if closed:
            additions.append(ClosingBracket())
        return self.clone_with_updates(append=additions)


class AsciiLetterCharacter(Range):
    def __call__(self, lowercase=True, **kwargs):
        start = 'a' if lowercase else 'A'
        end = 'z' if lowercase else 'Z'
        return super(AsciiLetterCharacter, self).__call__(start, end, **kwargs)


class Number(Range):
    def __call__(self, minimum=0, maximum=9, **kwargs) -> Pattern:
        if minimum >= maximum or minimum < 0 or maximum > 9:
            raise InvalidRangeError(
                f'Cannot build range between {minimum} and {maximum}'
            )
        return super(Number, self).__call__(minimum, maximum, **kwargs)


pattern = Pattern
