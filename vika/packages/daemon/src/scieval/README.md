# SciEval P1 Runtime adapter

This opt-in adapter converts the P1 `JobStore` outbox transition payload to a Vika connector event. It does not start automatically, read credentials, make network calls, poll, or change daemon wiring. Integrators must supply a trusted local bridge exposing asynchronous `pending()` and `acknowledge(eventId)` methods matching the Python `JobStore` semantics, and explicitly call `drain()`.

```js
const adapter = createSciEvalAdapter({
  enabled: true,
  outbox: localP1Bridge,
  submit: harnessSubmitter(harness, harness.store),
});
await adapter.drain();
```

`harnessSubmitter` requires the actual harness and its matching store. It verifies the event is present in the Runtime's persistent ledger, is not filtered, and has the expected payload before acknowledging the source. The harness itself does not report durable acceptance. Replays keep the P1 event ID, so Runtime deduplicates a crash between ingestion and acknowledgement. A failed or filtered event stops the drain and stays pending. The P1 outbox does not have occurrence timestamps; `occurred_at` is the ingestion attempt time. No external delivery, completion of agent processing, authenticated identity, or distributed exactly-once guarantee is implied by ledger acceptance. The local P1 bridge and startup integration remain to be implemented and tested against real services.
