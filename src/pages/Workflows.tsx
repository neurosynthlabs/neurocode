import { Page, PageHeader, PageBody, Empty } from '@/components/os';

export default function Workflows() {
  return (
    <Page>
      <PageHeader title="Workflows" subtitle="Pending build." />
      <PageBody><Empty title="Workflows" hint="This screen has not been implemented yet." /></PageBody>
    </Page>
  );
}
