import type { Metadata } from "next";
import { ApprovalsView } from "@/components/views/approvals";

export const metadata: Metadata = { title: "Approvals" };

export default function Page() {
  return <ApprovalsView />;
}
