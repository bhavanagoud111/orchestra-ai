import type { Metadata } from "next";
import { AgentsView } from "@/components/views/agents";

export const metadata: Metadata = { title: "Agents" };

export default function Page() {
  return <AgentsView />;
}
