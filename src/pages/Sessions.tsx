import { Page, PageHeader, PageBody, Empty } from '@/components/os';

export default function Sessions() {
  return (
    <Page>
      <PageHeader title="Sessions" subtitle="Pending build." />
      <PageBody><Empty title="Sessions" hint="This screen has not been implemented yet." /></PageBody>
    </Page>
  );
}
