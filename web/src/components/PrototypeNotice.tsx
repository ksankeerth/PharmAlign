// Shown wherever the knowledge library can show document text. The text is the publisher's: the app only demonstrates with it.
import { Callout } from "./Panel";

export function PrototypeNotice({ sourceUrl }: { sourceUrl?: string | null }) {
  return (
    <Callout tone="neutral" icon="book" title="For this prototype only, not for republication">
      Documents are here to show how the platform works. They belong to their publishers, under the publishers' own terms. To read, quote or rely on a document, use the official source
      {sourceUrl ? (
        <>
          : <a href={sourceUrl} target="_blank" rel="noreferrer noopener">open the official source</a>.
        </>
      ) : (
        "."
      )}
    </Callout>
  );
}
