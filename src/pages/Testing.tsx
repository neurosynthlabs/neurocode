import { Page, PageHeader, PageBody, Empty } from '@/components/os';

export default function Testing() {
  return (
    <Page>
      <PageHeader title="Testing" subtitle="Pending build." />
      <PageBody><Empty title="Testing" hint="This screen has not been implemented yet." /></PageBody>
    </Page>
  );
}
