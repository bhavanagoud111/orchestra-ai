import type { Metadata } from "next";
import { WorkflowsView } from "@/components/views/workflows";

export const metadata: Metadata = { title: "Workflows" };

export default function Page() {
  return <WorkflowsView />;
}
