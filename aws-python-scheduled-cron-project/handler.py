import boto3
import os
import datetime
import logging
from botocore.exceptions import ClientError
from operator import itemgetter


sns = boto3.client('sns')
rds = boto3.client('rds', region_name=os.environ.get('DB_REGION'))


def create_snapshot (db_identifier):
    try:
        creation_ts = datetime.datetime.now().strftime("%m-%d-%Y-T%H%M%S")
        create_db_snapshot = rds.create_db_snapshot(
            DBSnapshotIdentifier = f"{db_identifier}-{creation_ts}",
            DBInstanceIdentifier = db_identifier,
            Tags = [
                {
                    'Key': 'CreatedBy',
                    'Value': 'Lambda'
                },
                {
                    'Key': 'Name',
                    'Value': 'nc-production-database'
                },
            ]
        )
        snapshot_id = list(create_db_snapshot['DBSnapshot'].values())[0]
        waiter = rds.get_waiter('db_snapshot_available')
        waiter.wait( DBInstanceIdentifier = db_identifier, DBSnapshotIdentifier=snapshot_id, WaiterConfig={'Delay': 5, 'MaxAttempts': 100} )
        created_db_snapshot = rds.describe_db_snapshots(
            DBSnapshotIdentifier=snapshot_id
        )
        snapshot_status = created_db_snapshot['DBSnapshots'][0]['Status']
        
        return snapshot_id, snapshot_status
    except:
        logging.exception("The detailed error message:")
        return False
        
    
def share_snapshot(db_identifier, snapshot_id):
    try:
        shared_account = os.environ.get('SHARED_ACCOUNT')
        rds.modify_db_snapshot_attribute( 
            DBSnapshotIdentifier=snapshot_id, 
            AttributeName='restore', 
            ValuesToAdd=[shared_account]
        )
        return snapshot_id
    except:
        logging.exception("The detailed error message:")
        return False

def delete_old_snapshots (db_identifier):
    try:
        deleted_snapshots = ['null']
        retentionDate = datetime.datetime.now() - datetime.timedelta(days=7)
        manual_db_snapshots = rds.describe_db_snapshots(
            DBInstanceIdentifier = db_identifier,
            SnapshotType='manual',
            MaxRecords=50)
        snapshot_list = manual_db_snapshots['DBSnapshots']
        for snapshot in snapshot_list:
            create_ts = snapshot['SnapshotCreateTime'].replace(tzinfo=None)
            tag = snapshot['TagList']
            for i in tag:
                if i['Key'] == 'CreatedBy' and i['Value'] == 'Lambda':
                    if create_ts < retentionDate:
                        rds.delete_db_snapshot(DBSnapshotIdentifier = snapshot['DBSnapshotIdentifier'])
                        deleted_snapshots.append(snapshot['DBSnapshotIdentifier'])
        return deleted_snapshots
    except:
        logging.exception("The detailed error message:")
        return False
        

def publish_message(topic_arn, message, subject):
    try:
        response = sns.publish(
            TopicArn=topic_arn,
            Message=message,
            Subject=subject
        )
    except:
        logging.exception("The detailed error message:")


def lambda_handler(event, context):
    topic_arn = os.environ.get('TOPIC_ARN')
    db_identifier = os.environ.get('DB_IDENTIFIER')
    snapshot = create_snapshot(db_identifier)
    if snapshot:
        print ("Snapshot status:", snapshot[1])
        share = share_snapshot(db_identifier, snapshot[0])
        if share:
            print ("Shared snapshot identifier:", snapshot[0])
            delete = delete_old_snapshots(db_identifier)
            if delete and delete != ['null']:
                print ('Deleted snapshot(s):', '\n'.join(map(str, delete[1:])))
                message = "Successfully created and shared RDS snapshot. Old RDS snapshot deleted.\n\n\nShared snapshot identifier:  " +  snapshot[0] + "\nCreated snapshot status:  " + snapshot[1]
                publish_message(topic_arn, message, '[SUCCESS]-Back up, share and delete RDS snapshot')
            elif delete == ['null']:
                print ('Deleted snapshot(s): no RDS snapshot to delete ')
                message = "Successfully created and shared RDS snapshot. No RDS snapshot to delete.\n\n\nShared snapshot identifier:  " +  snapshot[0] + "\nCreated snapshot status:  " + snapshot[1]
                publish_message(topic_arn, message, '[SUCCESS]-Back up, share and delete RDS snapshot')
            else:
                message = "Failed to delete snapshots.\n\n\nShared snapshot identifier:  " +  snapshot[0] + "\nCreated snapshot status:  " + snapshot[1]
                publish_message(topic_arn, message, '[FAILED]-Delete old RDS snapshot')
        else:
            message = "Failed to share snapshot.\n\n\nSnapshot identifier:  " +  snapshot[0] + "\nSnapshot status:  " + snapshot[1]
            publish_message(topic_arn, message, '[FAILED]-Share RDS snapshot')
    else:
        publish_message(topic_arn, 'Failed to create snapshot', '[FAILED]-Back up RDS snapshot')