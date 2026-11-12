/**
 * AG Grid Community setup (MIT licensed). Community modules only: no Enterprise
 * packages, modules, licence keys or trial features are used anywhere.
 *
 * Only the modules these grids use are registered, which keeps the bundle small
 * (AllCommunityModule would add every Community feature). ValidationModule is
 * registered in development/tests only, so a missing module is reported loudly
 * there instead of failing silently.
 */
import {
  CellStyleModule,
  ClientSideRowModelModule,
  DateFilterModule,
  ModuleRegistry,
  NumberFilterModule,
  PaginationModule,
  RowAutoHeightModule,
  TextFilterModule,
  ValidationModule,
  themeQuartz,
} from 'ag-grid-community'

ModuleRegistry.registerModules([
  ClientSideRowModelModule,
  PaginationModule,
  TextFilterModule,
  NumberFilterModule,
  DateFilterModule,
  CellStyleModule,
  RowAutoHeightModule,
  ...(import.meta.env.DEV ? [ValidationModule] : []),
])

/** Quartz theme tuned to the app's design tokens (see :root in index.css). */
export const gridTheme = themeQuartz.withParams({
  fontFamily: 'inherit',
  fontSize: 13,
  headerFontWeight: 600,
  accentColor: '#4f46e5',
  foregroundColor: '#0f172a',
  backgroundColor: '#ffffff',
  borderColor: '#e4e8f0',
  headerBackgroundColor: '#f8fafc',
  rowHoverColor: '#eef2ff',
  wrapperBorderRadius: 12,
  spacing: 6,
  browserColorScheme: 'light',
})

/** Defaults shared by every grid: Community sorting, filtering and resizing. */
export const defaultColDef = {
  sortable: true,
  filter: true,
  floatingFilter: true,
  resizable: true,
  minWidth: 110,
  flex: 1,
} as const
