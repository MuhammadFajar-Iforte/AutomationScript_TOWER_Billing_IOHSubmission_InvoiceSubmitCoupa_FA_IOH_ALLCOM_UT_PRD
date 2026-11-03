-- -- Query untuk mengambil data invoice yang akan diproses

-- SELECT 
--     TLP_PROVIDER_FULL_NAME AS [Tower Provider Name], 
--     RPA_PONUMBER_IOH AS [PO Number], 
--     INVOICE_NUMBER AS [Invoice Number], 
--     -- Invoice Description: gunakan Invoice Number sebagai default
--     Payment_system AS [Invoice Description],
--     TYPE_OF_BILL AS [Status Site], 
--     -- BA Number: jika Recurring maka Invoice Number, selain itu BA Number
--     CASE 
--         WHEN TYPE_OF_BILL = 'Recurring' THEN INVOICE_NUMBER
--         ELSE ISNULL(Program, INVOICE_NUMBER)
--     END AS [BA Number],
--     TAX_NUMBER AS [Tax Invoice Number], 
--     TAX_INVOICE_DATE AS [Tax Invoice Date], 
--     INVOICE_AMOUNT AS [Invoice Amount],
--     IOH_SITE_ID AS [IOH Site ID], 
--     SITE_ID_ACTIVE AS [IOH Site ID Active],
-- 	RPA_PPH_SAP as [WHT Code],
--     PAYMENT_PERIOD_START as [Payment Period Start],
--     Line_Number_ItemPO as [Invoice Line Number],
--     QTY AS [Qty]
-- FROM TrackerProcess_Billing_IOH
-- where RPA_COUPA_STATUS is null


SELECT
    header.ENTITY AS [Tower Provider Name], 
	header.PAYMENT_PERIOD_START,
    header.RPA_PONUMBER_IOH AS [PO Number], 
    detail.INVOICE_NUMBER AS [Invoice Number], 
    -- Invoice Description: gunakan Payment System
    detail.RPA_INVOICE_DESCRIPTION AS [Invoice Description],
    header.TYPE_OF_BILL AS [Status Site], 
    -- BA Number: jika Recurring maka Invoice Number, selain itu BA Number
    CASE 
        WHEN header.TYPE_OF_BILL = 'Recurring' THEN detail.INVOICE_NUMBER
        ELSE ISNULL(header.Program, detail.INVOICE_NUMBER)
    END AS [BA Number],
    detail.TAX_NUMBER AS [Tax Invoice Number], 
    detail.TAX_INVOICE_DATE AS [Tax Invoice Date], 
    detail.RPA_INVOICE_AMOUNT AS [Invoice Amount],
    header.IOH_SITE_ID AS [IOH Site ID], 
    header.SITE_LEASE_ID_ACTIVE AS [IOH Site ID Active],
    header.RPA_PPH_SAP AS [WHT Code],
    header.PAYMENT_PERIOD_START AS [Payment Period Start],
    header.Line_Number_ItemPO AS [Invoice Line Number],
    detail.QTY AS [Qty],
	header.RPA_COUPA_DATE, 
	header.RPA_COUPA_STATUS,
	header.RPA_MERGEFILEEMAIL_LINKFILE
FROM TrackerProcess_Billing_IOH_Clone AS header
INNER JOIN TrackerProcess_BillingDetails_IOH_Clone AS detail
    ON header.SITE_LEASE_ID_ACTIVE = detail.SITE_LEASE_ID_ACTIVE
WHERE  header.RPA_CREATESUPDOC_STATUS  = 'Success' and detail.INVOICE_NUMBER is not null and header.RPA_MERGEFILEEMAIL2_STATUS LIKE '%success%'
-- WHERE detail.INVOICE_NUMBER is not null and header.RPA_PONUMBER_IOH in ('4810088405',
-- '4810088406',
-- '4810088423',
-- '4810088692')

