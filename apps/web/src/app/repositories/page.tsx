import type { Metadata } from "next";
import { RepositoriesView } from "@/components/views/repositories";

export const metadata: Metadata = { title: "Repositories" };

export default function Page() {
  return <RepositoriesView />;
}
