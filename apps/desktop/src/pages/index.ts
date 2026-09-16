import { memo } from "react";
import { OverviewPage as OverviewPageImpl, type OverviewPageProps, type OverviewPipelineNode } from "./OverviewPage";
import { ProductionPage as ProductionPageImpl, type ProductionPageProps } from "./ProductionPage";
import { ReviewPage as ReviewPageImpl, type ReviewPageProps } from "./ReviewPage";
import { PublishPage as PublishPageImpl, type PublishPageProps } from "./PublishPage";
import { MessagesPage as MessagesPageImpl, type MessagesPageProps } from "./MessagesPage";
import { DataCenterPage as DataCenterPageImpl, type DataCenterPageProps } from "./DataCenterPage";
import { OpsPage as OpsPageImpl, type OpsPageProps } from "./OpsPage";
import { SettingsPage as SettingsPageImpl, type SettingsPageProps } from "./SettingsPage";
import { LogsPage as LogsPageImpl } from "./LogsPage";

/** memo: skip reconcile of hidden keep-alive tabs when App only flips `tab`. */
export const OverviewPage = memo(OverviewPageImpl);
export const ProductionPage = memo(ProductionPageImpl);
export const ReviewPage = memo(ReviewPageImpl);
export const PublishPage = memo(PublishPageImpl);
export const MessagesPage = memo(MessagesPageImpl);
export const DataCenterPage = memo(DataCenterPageImpl);
export const OpsPage = memo(OpsPageImpl);
export const SettingsPage = memo(SettingsPageImpl);
export const LogsPage = memo(LogsPageImpl);

export type {
  OverviewPageProps,
  OverviewPipelineNode,
  ProductionPageProps,
  ReviewPageProps,
  PublishPageProps,
  MessagesPageProps,
  DataCenterPageProps,
  OpsPageProps,
  SettingsPageProps,
};
export type {
  AskConfirmFn,
  LangCatalogItem,
  NotifyFn,
  OllamaInfo,
  OpsReportView,
  TitlePoolSummary,
} from "./pageTypes";
