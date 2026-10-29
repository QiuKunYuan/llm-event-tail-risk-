// RESSET 批量导出任务清单
// 每个任务：表名、dlm、dbMsgId、日期字段、日期范围、字段列表
const TASKS = [
  // 1. 业绩预告 - 已完成 ✅
  // 2. 诉讼仲裁（事件抽取第二通道）
  {
    name: '诉讼仲裁_扩展',
    tableName: 'SUITARBTR_EXT', dlm: '114',
    dbMsgId: '4028818a2206ecbc012206edd0d10001',
    dateObject: 'dList_InfoPubDt', begin: '2015-01-01', end: '2025-12-31',
    fields: ['nobs','ID','CompanyCode','ComCd','LComNm','A_StkCd','IInfoPubDt','InfoPubDt','DclrType','RlsMtd','EventCon','ActDes','NewestAdv','EventSub','EventProc','ActWay','SubNm','ObjNm']
  },
  // 3. 主要股东名单与股权结构（共同股东图）
  {
    name: '主要股东名单与股权结构',
    tableName: 'MSHLSTANDOS', dlm: '106',
    dbMsgId: '4028818a2206ecbc012206edd0d10001',
    dateObject: 'dList_InfoPubDt', begin: '2015-01-01', end: '2025-12-31',
    fields: ['nobs','ID','ComCd','LComNm','A_StkCd','YrFlg','QtrFlg','InfoPubDt','EndDt','InfoSource','SHLst','SHSer','GDID','ShRank','SHLst2','SHAttrib','SHChrctCd','SHChrct','SHTypeCd','SHType','HoldSum','PctTotShr','PctTrdShr','ComFullShr','TrdShr','HoldChgTp','HoldSumChg','HoldSumChgRt','UpDt']
  },
  // 4. 日收益（风险标签基础）2015-2025，需要分两段
  {
    name: '日收益2015-2020',
    tableName: 'DRET_2016_2020', dlm: 'ff808081432dd4cd01434264d2951117',
    dbMsgId: '4028818a2206ecbc012206edd0d10001',
    dateObject: 'dList_Date', begin: '2016-01-01', end: '2020-12-31',
    fields: ['nobs','Comcd','CompanyCode','Stkcd','Lstknm','Stkcdotrd','Csrciccd1','Csrciccd2','Date','Dret','Daret','Dreteq','Drettmv','Dretmc','DRfRet']
  },
  {
    name: '日收益2021-2025',
    tableName: 'DRET_2021_2025', dlm: 'ff808081432dd4cd01434264d2951117',
    dbMsgId: '4028818a2206ecbc012206edd0d10001',
    dateObject: 'dList_Date', begin: '2021-01-01', end: '2025-12-31',
    fields: ['nobs','Comcd','CompanyCode','Stkcd','Lstknm','Stkcdotrd','Csrciccd1','Csrciccd2','Date','Dret','Daret','Dreteq','Drettmv','Dretmc','DRfRet']
  },
  {
    name: '日收益2015',
    tableName: 'DRET_2011_2015', dlm: 'ff808081432dd4cd01434264d2951117',
    dbMsgId: '4028818a2206ecbc012206edd0d10001',
    dateObject: 'dList_Date', begin: '2015-01-01', end: '2015-12-31',
    fields: ['nobs','Comcd','CompanyCode','Stkcd','Lstknm','Stkcdotrd','Csrciccd1','Csrciccd2','Date','Dret','Daret','Dreteq','Drettmv','Dretmc','DRfRet']
  },
  // 5. 日三因子（因子模型）
  {
    name: '日三因子',
    tableName: 'THRFACDAT_DAILY', dlm: '133',
    dbMsgId: '4028818a2206ecbc012206edd0d10001',
    dateObject: 'dList_Date', begin: '2015-01-01', end: '2025-12-31',
    fields: ['nobs','Exchflg','Mktflg','Date','Rmrf_tmv','Smb_tmv','Hml_tmv','Rmrf_mc','Smb_mc','Hml_mc']
  },
  // 6. 证监会门类行业日收益（行业控制）
  {
    name: '证监会门类行业日收益',
    tableName: 'DRET_INDUSTRY_1', dlm: 'ff80808147e6bead0147ebe2b09b024b',
    dbMsgId: '4028818a2206ecbc012206edd0d10001',
    dateObject: 'dList_Date', begin: '2015-01-01', end: '2025-12-31',
    fields: ['nobs','Date','Indcd','Indnm','Dret','Daret']
  },
  // 7. 证监会行业分类（历史行业匹配）
  {
    name: '证监会行业分类',
    tableName: 'CSRCINDCLS', dlm: '103',
    dbMsgId: '4028818a2206ecbc012206edd0d10001',
    dateObject: 'dList_InfoPubDt', begin: '2015-01-01', end: '2025-12-31',
    fields: ['nobs','ComCd','LComNm','A_StkCd','InfoPubDt','Csrciccd1','Csrciccd1nm','Csrciccd2','Csrciccd2nm']
  },
  // 8. 上市状态变更历史（ST/退市处理）
  {
    name: '上市状态变更历史',
    tableName: 'LSTSTTHIS', dlm: '104',
    dbMsgId: '4028818a2206ecbc012206edd0d10001',
    dateObject: 'dList_ChgDt', begin: '2015-01-01', end: '2025-12-31',
    fields: ['nobs','ComCd','A_StkCd','ChgDt','BfStkStat','AfStkStat','LstStat']
  },
  // 9. 日换手率（节点特征）分两段
  {
    name: '日换手率2016-2020',
    tableName: 'DTURNR_2016_2020', dlm: '12801',
    dbMsgId: '4028818a2206ecbc012206edd0d10001',
    dateObject: 'dList_Date', begin: '2016-01-01', end: '2020-12-31',
    fields: ['nobs','Comcd','Stkcd','Date','FulTurnR','TrdTurnR']
  },
  {
    name: '日换手率2021-2025',
    tableName: 'DTURNR_2021_2025', dlm: '12801',
    dbMsgId: '4028818a2206ecbc012206edd0d10001',
    dateObject: 'dList_Date', begin: '2021-01-01', end: '2025-12-31',
    fields: ['nobs','Comcd','Stkcd','Date','FulTurnR','TrdTurnR']
  },
  {
    name: '日换手率2015',
    tableName: 'DTURNR_2011_2015', dlm: '12801',
    dbMsgId: '4028818a2206ecbc012206edd0d10001',
    dateObject: 'dList_Date', begin: '2015-01-01', end: '2015-12-31',
    fields: ['nobs','Comcd','Stkcd','Date','FulTurnR','TrdTurnR']
  },
  // 10. 日市值（节点特征）分两段
  {
    name: '日市值2016-2020',
    tableName: 'DMV_2016_2020', dlm: '129',
    dbMsgId: '4028818a2206ecbc012206edd0d10001',
    dateObject: 'dList_Date', begin: '2016-01-01', end: '2020-12-31',
    fields: ['nobs','Comcd','Stkcd','Date','Totmv','Trdsmv','Trdmv']
  },
  {
    name: '日市值2021-2025',
    tableName: 'DMV_2021_2025', dlm: '129',
    dbMsgId: '4028818a2206ecbc012206edd0d10001',
    dateObject: 'dList_Date', begin: '2021-01-01', end: '2025-12-31',
    fields: ['nobs','Comcd','Stkcd','Date','Totmv','Trdsmv','Trdmv']
  },
  {
    name: '日市值2015',
    tableName: 'DMV_2011_2015', dlm: '129',
    dbMsgId: '4028818a2206ecbc012206edd0d10001',
    dateObject: 'dList_Date', begin: '2015-01-01', end: '2015-12-31',
    fields: ['nobs','Comcd','Stkcd','Date','Totmv','Trdsmv','Trdmv']
  },
  // 11. 日波动率（节点特征）分两段
  {
    name: '日波动率2016-2020',
    tableName: 'DVOLATILITY_2016_2020', dlm: '132',
    dbMsgId: '4028818a2206ecbc012206edd0d10001',
    dateObject: 'dList_Date', begin: '2016-01-01', end: '2020-12-31',
    fields: ['nobs','Comcd','Stkcd','Date','DVolatility']
  },
  {
    name: '日波动率2021-2025',
    tableName: 'DVOLATILITY_2021_2025', dlm: '132',
    dbMsgId: '4028818a2206ecbc012206edd0d10001',
    dateObject: 'dList_Date', begin: '2021-01-01', end: '2025-12-31',
    fields: ['nobs','Comcd','Stkcd','Date','DVolatility']
  },
  {
    name: '日波动率2015',
    tableName: 'DVOLATILITY_2011_2015', dlm: '132',
    dbMsgId: '4028818a2206ecbc012206edd0d10001',
    dateObject: 'dList_Date', begin: '2015-01-01', end: '2015-12-31',
    fields: ['nobs','Comcd','Stkcd','Date','DVolatility']
  },
  // 12. 月惯性因子（动量，仅月频——自行构造日频）
  {
    name: '月惯性因子30分位',
    tableName: 'MOMFAC_MONTHLY_30PER', dlm: 'ff8080814ddae396014ddafbe0380018',
    dbMsgId: '4028818a2206ecbc012206edd0d10001',
    dateObject: 'yList_Date', begin: '2015', end: '2025',
    fields: ['nobs','ExchFlg','MktFlg','Date','MomFac_Eq','MomFac_TMV']
  }
];
window.__TASKS = TASKS;
console.log('定义了' + TASKS.length + '个导出任务');
