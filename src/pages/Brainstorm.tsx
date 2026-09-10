import { Page, PageHeader, PageBody, Empty } from '@/components/os';

export default function Brainstorm() {
  return (
    <Page>
      <PageHeader title="Brainstorm" subtitle="Pending build." />
      <PageBody><Empty title="Brainstorm" hint="This screen has not been implemented yet." /></PageBody>
    </Page>
  );
}
