import { QueryClient } from '@tanstack/react-query'

type QueryClientHotData = { queryClient?: QueryClient }
const hotData = import.meta.hot?.data as QueryClientHotData | undefined

export const queryClient = hotData?.queryClient ?? new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 15_000,
      refetchOnWindowFocus: false,
    },
  },
})

if (import.meta.hot) {
  import.meta.hot.dispose((data: QueryClientHotData) => {
    data.queryClient = queryClient
  })
}
