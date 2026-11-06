import { ButtonLink, EmptyState, PageHeader } from "../../components";

export default function NotFoundPage() {
  return (
    <div className="stack">
      <PageHeader title="Page not found" />
      <EmptyState title="There is no screen at this address" icon="search" action={<ButtonLink to="/" icon="home">Go to my home screen</ButtonLink>}>
        The link may be out of date, or the screen belongs to another area.
      </EmptyState>
    </div>
  );
}
