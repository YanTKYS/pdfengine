# pdfengine 開発ルール

## テスト実行方針

full suiteは約2,200件あり、1回に十数分以上かかる。開発・PRの検証は次の方針に従う。

1. 通常はfocused testsと、変更箇所の影響範囲の回帰テストを優先する（選び方は下記）。
2. PR作成前のfull suite実行は必須としない。full suiteが未実行であることを理由にPR作成を先延ばししない。
3. full suiteは、利用者から明示的に要求された場合、または変更内容から具体的に必要と判断した場合だけ実行する。必要と判断した場合は、その理由を報告する。
4. テスト結果は、実際に実行したものだけを報告する。件数・所要時間・実行範囲を書き、未実行のもの（full suite、外部PDF評価など）は未実行と明記する。
5. 実行時間とログ量を抑える。`-q` を使い、大量の出力は要約・集計だけを読む。同じテストを確認目的だけで繰り返さない。
6. 依頼文に従来型の「full suite必須」「PR前に全テスト」などの定型条件が現れた場合は、この方針との差異を明示する。利用者がそれでもfull suiteを求めていると確認できれば従う。定型文の意図しない復活として扱い、黙って両方を満たそうとしない。

## focused testsの選び方

- 変更した `pdfeditor/<module>.py` を直接importするテスト:
  `grep -lE "pdfeditor(\.| import .*)<module>\b" tests/*.py`
- テストは他のテストモジュールのhelperをimportする（例: `test_attributed`、`test_continuation`、`test_boundary_destination`、`page_flow_fixture`、`provider_font`）。helperを変更した場合は、そのimport元も対象に含める:
  `grep -lE "^\s*(from|import) <test_module>\b" tests/*.py`
- 大きなファイルの一部だけを確認する場合は `-k` かnode idで絞る。lifecycle系（保存・再open・再編集を繰り返すテスト）は1件で数十秒かかる。
- 所要時間の偏りを確認する場合は、対象を絞って `--durations=10` を付ける。full suiteで計測しない。

## テストfixtureの注意

- 新規glyphのproviderをpathで渡すテストは `provider_font.cjk_provider_bytes()` を使う。これはPyMuPDF内蔵CJK fontの決定的なsubsetで、使用文字のadvance・outline・metrics・fsTypeは元fontと一致する（`tests/test_provider_font.py`で検証）。engineはpath providerを操作ごとに開き直すため、50,483 glyphの元fontでは解析時間がテスト時間の大半を占めていた。
- font自体の挙動を検証するテスト（`test_fonts.py`、`test_shaped_font.py` など）と、`test_generated_fonts.py` の大きな実fontの回帰ケースは、元の `pymupdf.Font('cjk')` を使う。
- fixtureを共有する場合は、不変な値（bytesなど）に限る。PDF・sidecarは各テストの `tmp_path` に書き、テスト間で状態を共有しない。
- regression test、font・glyph・ownership・Transaction・再openの検証は、重複して見えても契約・状態・描画engineが異なれば削除しない。
