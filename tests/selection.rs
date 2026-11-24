use teleprint::{Hit, Transcript, plain};

#[test]
fn select_visible_block_or_wrapped_draft() {
    let mut view = Transcript::new(12, 5);
    let blocks = [("input", "first\nsecond"), ("result", "\x1b[31mA\tB\n42\x1b[0m\x1b]0;ignored title\x07")];
    let draft = "> 1234567890x";
    view.sync(&blocks, draft, Some(draft.len()));
    assert!(matches!(view.hit(-4), Some(Hit::Block("input"))));
    let Some(Hit::Block(key)) = view.hit(-2) else { panic!("missing result row") };
    let (_, source) = blocks.iter().find(|(k, _)| *k == key).unwrap();
    assert_eq!(plain(source), "A\tB\n42");
    assert!(matches!(view.hit(-1), Some(Hit::Tail(0))));
    assert!(matches!(view.hit(0), Some(Hit::Tail(1))));

    let draft = "a\nb\nc\nd\ne\nf";
    view.sync(&blocks, draft, Some(draft.len()));
    assert!(matches!(view.hit(-4), Some(Hit::Tail(1))));
    assert!(view.hit(1).is_none());
}
