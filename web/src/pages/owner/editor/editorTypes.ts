import type { PackDocument, PackIssue } from "../../../api/types";
import type { Location } from "../packEdit";

/** What every editor tab receives. `update` edits a copy of the draft and marks it unsaved. */
export interface EditorProps {
  doc: PackDocument;
  update: (mutate: (draft: PackDocument) => void) => void;
  readOnly: boolean;
  /** Issues found by checking the unsaved draft in the browser. */
  issues: PackIssue[];
  /** Where a validation link asked to land. */
  focus: Location | null;
  /** Library document ids and titles, for source pickers. */
  docOptions: { id: string; title: string }[];
}
