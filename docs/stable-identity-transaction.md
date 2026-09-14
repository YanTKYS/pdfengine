# 安定identityと単一transaction

2026-09-14。[fable5.1-review1.md](fable5.1-review1.md) のロードマップ第1段階。目的は新機能ではなく、以後の機能追加で作り直しにならない実行基盤への変更である。

## Identityの形

identityは「同一revisionの page program 内の位置」とする。文字は (operator の byte offset, operator 内の文字index)、path paint は (paint operator の byte offset)。既存の `ContentPage` の byte-range provenance と `paint_provenance` の目録がそのまま位置の出所であり、新しいID体系は作らない。

revision をまたぐ対応は `pdfeditor/mutation.py` の `MutationProgram` が持つ。1ページの全 byte mutation（`Mutation(start, end, data, anchors, chars)`）を1か所に集め、`map_offset` で mutation 外の offset を前方へ写像する。mutation 内の要素は anchor でだけ追跡できる。`op` は wrapper 内に保持した元 operator、`rewritten` は書き直した text operator（`chars` が保持文字の新旧index）、`glyph:n` / `paint:n` は生成した operator である。

写像後は必ず witness を照合する。文字は font resource 名・code・Unicode・glyph ID・paint 種別、path は operator bytes の一致（生成 paint は anchor）。一致しない・重複する・mutation に消費された要素は `PdfError` で拒否し、幾何や並び順による再結合は行わない。`document_flow._rebind` の `zip(old_paths, new_paths)` と origin 一致による glyph 探索は廃止した。

`IdentityMap` は before/after の `ContentPage`、`MutationProgram`、計画済み paint 幾何（移動・伸縮）、生成 paint に置換されて消費された path をまとめ、`map_glyphs` / `map_path` / `emitted_glyphs` / `emitted_paths` を提供する。

永続化する記録は二層ある。`byte_edits`（`Mutation.edit`）は start / end / length と保持 operator の offset だけの byte 位置写像で、mutation の外側の要素にしか使えない。`mutation_map`（`Mutation.record`）はそれに kind / owner / 全 anchor / `chars` を加えた identity 記録で、report に載る。保存済み PDF と `mutation_map` から `IdentityMap.from_records` で再構築すると、mutation 外の要素、書き直した operator 内の保持文字、生成 glyph、生成 paint operator の位置は元の transaction と同じ写像が得られる。記録が保存 program を再現しないときは拒否する。

記録が持つのは位置と anchor までである。「どの元 path がどの生成 paint に置き換わったか」（`path_anchors`）、生成 paint に置換されて単一の後継を持たない path（`consumed_paths`）、移動・伸縮後の期待幾何（`planned_paints`）は transaction の意味情報であり、live の `IdentityMap` だけが持つ。flow の再結合はすべて transaction 内の live map で行うため、この分離は現在の利用モデルで問題にならない。記録から再構築した map でこれらが必要な場合は、呼び出し側が mutation の kind と anchor 名から明示的に与える。

`MutationProgram` の規則: 空でない mutation は隣接（一方の end が他方の start）してよい。長さ 0 の挿入は他の mutation の start / end に触れてはならず、重なりや同一位置の複数挿入とともに拒否する。挿入位置の元 byte は挿入の後ろに写像される。

## 単一transaction

`pdfeditor/transaction.py` の `Transaction` は1つの元 revision に対する plan の集合である。plan は既存 writer を分割したもので、元PDF上の証明（no-op replay、除去監査、counterfactual paint provenance、font subset 検証）を各自で行い、byte mutation・消費する glyph・移動する glyph・変更する paint・影響領域・最終占有領域を宣言する。

| plan | 元の関数 | 主な mutation |
|---|---|---|
| `ParagraphPlan` (`paragraph.plan_paragraph_edit`) | `edit_paragraph` | 選択 operator の書き直し、生成 glyph の `Tm`/`Tj`、下線 segment |
| `MovePlan` (`elements.plan_element_move`) | `move_element` | `q cm … Q` による text wrapper、path token の再生 |
| `ResizePlan` (`paint_resize.plan_paint_resize`) | `resize_paints` | band 伸縮した path token |

`commit` は次を1回だけ行う。最終状態での障害検査（他 plan が消費・移動する glyph を除外し、他 plan の計画 paint 幾何に対して検査）、plan 間の最終占有領域の非交差（自らの background と宣言した paint の移動・伸長だけを免除）、全 mutation の適用、複数ページを含む1回の保存、保存結果の検証（未変更 glyph の不変、移動 glyph の平行移動、生成 glyph の plan 一致、font resource、非text paint の計画一致、影響領域外の画素一致）。

`edit_paragraph` / `move_element` / `resize_paints` / `write_editable` / `edit_document` は単一 plan の transaction として同じ API を保つ。`edit_flow` / `edit_flow_batch` / `edit_story` / `edit_shared_flow` / `edit_variable_container` は全 plan を1つの transaction に載せ、中間 PDF を作らない。編集後の binding は `IdentityMap` を通して再構築する（`document_flow.rebind_entry`、`editable.bind_editable`）。

## anchorsとflow

下線 anchor は生成 paint の anchor（`paint:n`）で識別されるため、要素移動・他要素の編集と同じ identity map 上で扱える。`confirm_document` は要素ごとの `anchors` 指定を受け、`edit_flow` / `edit_flow_batch` で anchored 要素の再組版・平行移動・他要素の編集を同一 transaction で行う。story / shared flow の跨領域装飾は従来どおり拒否する。

## 変わらないこと

provenance の証明は省略していない。paragraph ごとの replay・除去監査、path ごとの counterfactual 証明、保存結果の再観測はそのまま残る。sidecar の schema は維持し、identity の継続だけを mutation map に移した。
